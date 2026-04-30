from builtins import list, print, type
import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import sys

import litellm

litellm.suppress_debug_info = True
litellm.REPEATED_STREAMING_CHUNK_LIMIT = 99999999

import json
import logging
import subprocess
import time
import uuid

import requests
import tokentrim as tt

from .run_text_llm import run_text_llm

# from .run_function_calling_llm import run_function_calling_llm (UNUSED)
from .run_tool_calling_llm import run_tool_calling_llm
#from .utils.convert_to_openai_messages import convert_to_openai_messages
from .utils.convert_to_openai_responses_messages import convert_to_openai_responses_messages # Responses

#print("Starting llm.py")

# Create or get the logger
logger = logging.getLogger("LiteLLM")


class SuppressDebugFilter(logging.Filter):
    def filter(self, record):
        # Suppress only the specific message containing the keywords
        if "cost map" in record.getMessage():
            return False  # Suppress this log message
        return True  # Allow all other messages


class Llm:
    """
    A stateless LMC-style LLM with some helpful properties.
    """

    def __init__(self, interpreter):
        # Add the filter to the logger
        logger.addFilter(SuppressDebugFilter())

        # Store a reference to parent interpreter
        self.interpreter = interpreter

        # OpenAI-compatible chat completions "endpoint"
        self.completions = fixed_litellm_completions

        # Settings
        #self.model = "gpt-4o"
        self.model = "gpt-5" # Reasoning-capable
        self.temperature = 0.0

        self.supports_vision = None  # Will try to auto-detect
        self.vision_renderer = (
            self.interpreter.computer.vision.query
        )  # Will only use if supports_vision is False

        self.supports_functions = None  # Will try to auto-detect
        self.execution_instructions = "To execute code on the user's machine, write a markdown code block. Specify the language after the ```. You will receive the output. Use any programming language."  # If supports_functions is False, this will be added to the system message

        # Optional settings
        self.context_window = None
        self.max_tokens = None
        self.api_base = None
        self.api_key = None
        self.api_version = None
        self._is_loaded = False

        # Budget manager powered by LiteLLM
        self.max_budget = None

        # Reasoning effort passthrough (providers that support it: e.g., OpenAI o4)
        self.reasoning_effort = None  # "low" | "medium" | "high"

        # Responses API continuation state. After a streamed response completes,
        # the next request can reference it instead of replaying tool-call-shaped
        # assistant history back into the model.
        self.previous_response_id = None
        self.previous_response_message_count = None
        self.pending_tool_call_id = None

    def _ensure_responses_message_shape(self, m):
        """
        Coerce a chat-like message into a valid Responses input item:
        - { role: one of user/assistant/system/developer, content: [parts...] }
        - Text parts use input_text (non-assistant) or output_text (assistant)
        - Strip Chat Completions-only fields
        - Map legacy roles ('tool', 'function') to 'user'
        """
        if m.get("type") == "function_call_output":
            return {
                "type": "function_call_output",
                "call_id": m.get("call_id"),
                "output": m.get("output", ""),
            }

        # Remove Chat Completions-only baggage
        for k in ("tool_calls", "function_call", "tool_call_id", "name"):
            if k in m:
                m.pop(k, None)

        role = m.get("role", "user")
        if role in ("tool", "function"):           # ❗ Responses doesn't accept these
            role = "user"
        if role not in ("user", "assistant", "system", "developer"):
            role = "user"

        out = {"role": role}
        text_type = "output_text" if role == "assistant" else "input_text"

        def _to_text_part(s):
            return {"type": text_type, "text": s if isinstance(s, str) else str(s)}

        c = m.get("content", "")

        # Already parts? normalize text parts and allow known non-text types
        if isinstance(c, list):
            parts = []
            for p in c:
                if isinstance(p, str):
                    parts.append(_to_text_part(p)); continue
                if not isinstance(p, dict):
                    parts.append(_to_text_part(p)); continue

                pt = p.get("type")
                if pt in ("text", "input_text", "output_text") and "text" in p:
                    parts.append({"type": text_type, "text": p["text"]}); continue

                if pt in {"input_image", "input_audio", "input_video", "input_json"}:
                    parts.append(p); continue

                parts.append(_to_text_part(p))
            out["content"] = parts if parts else [_to_text_part("")]
            return out

        # Plain string or other scalar
        if isinstance(c, str):
            out["content"] = [_to_text_part(c)]
        else:
            out["content"] = [_to_text_part(c)]
        return out

    def run(self, messages):
        """
        We're responsible for formatting the call into the llm.completions object,
        starting with LMC messages in interpreter.messages, going to OpenAI compatible messages into the llm,
        respecting whether it's a vision or function model, respecting its context window and max tokens, etc.

        And then processing its output, whether it's a function or non function calling model, into LMC format.
        """

        # print("[run] START. model=", self.model, "supports_functions=", self.supports_functions,
        #     "supports_vision=", self.supports_vision, flush=True)
        #print("[run] incoming messages (raw):", messages, flush=True)

        if not self._is_loaded:
            self.load()

        if (
            self.max_tokens is not None
            and self.context_window is not None
            and self.max_tokens > self.context_window
        ):
            print(
                "Warning: max_tokens is larger than context_window. Setting max_tokens to be 0.2 times the context_window."
            )
            self.max_tokens = int(0.2 * self.context_window)

        # Assertions
        assert (
            messages[0]["role"] == "system"
        ), "First message must have the role 'system'"
        for msg in messages[1:]:
            assert (
                msg["role"] != "system"
            ), "No message after the first can have the role 'system'"

        model = self.model
        if model in [
            "claude-3.5",
            "claude-3-5",
            "claude-3.5-sonnet",
            "claude-3-5-sonnet",
        ]:
            model = "claude-3-5-sonnet-20240620"
            self.model = "claude-3-5-sonnet-20240620"
        # Setup our model endpoint
        if model == "i":
            model = "openai/i"
            if not hasattr(self.interpreter, "conversation_id"):  # Only do this once
                self.context_window = 7000
                self.api_key = "x"
                self.max_tokens = 1000
                self.api_base = "https://api.openinterpreter.com/v0"
                self.interpreter.conversation_id = str(uuid.uuid4())

        # Detect function support
        if self.supports_functions == None:
            try:
                if litellm.supports_function_calling(model):
                    self.supports_functions = True
                else:
                    self.supports_functions = False
            except:
                self.supports_functions = False

        # Detect vision support
        if self.supports_vision == None:
            try:
                if litellm.supports_vision(model):
                    self.supports_vision = True
                else:
                    self.supports_vision = False
            except:
                self.supports_vision = False

        # Trim image messages if they're there
        #image_messages = [msg for msg in messages if msg["type"] == "image"]
        image_messages = [msg for msg in messages if msg.get("type") == "image"]
        #print(f"[run] image_messages found: {len(image_messages)}", flush=True)
        if self.supports_vision:
            if self.interpreter.os:
                # Keep only the last two images if the interpreter is running in OS mode
                if len(image_messages) > 1:
                    for img_msg in image_messages[:-2]:
                        messages.remove(img_msg)
                        if self.interpreter.verbose:
                            print("Removing image message!")
            else:
                # Delete all the middle ones (leave only the first and last 2 images) from messages_for_llm
                if len(image_messages) > 3:
                    for img_msg in image_messages[1:-2]:
                        messages.remove(img_msg)
                        if self.interpreter.verbose:
                            print("Removing image message!")
                # Idea: we could set detail: low for the middle messages, instead of deleting them
        elif self.supports_vision == False and self.vision_renderer:
            for img_msg in image_messages:
                if img_msg["format"] != "description":
                    self.interpreter.display_message("\n  *Viewing image...*\n")

                    if img_msg["format"] == "path":
                        precursor = f"The image I'm referring to ({img_msg['content']}) contains the following: "
                        if self.interpreter.computer.import_computer_api:
                            postcursor = f"\nIf you want to ask questions about the image, run `computer.vision.query(path='{img_msg['content']}', query='(ask any question here)')` and a vision AI will answer it."
                        else:
                            postcursor = ""
                    else:
                        precursor = "Imagine I have just shown you an image with this description: "
                        postcursor = ""

                    try:
                        image_description = self.vision_renderer(lmc=img_msg)
                        ocr = self.interpreter.computer.vision.ocr(lmc=img_msg)

                        # It would be nice to format this as a message to the user and display it like: "I see: image_description"

                        img_msg["content"] = (
                            precursor
                            + image_description
                            + "\n---\nI've OCR'd the image, this is the result (this may or may not be relevant. If it's not relevant, ignore this): '''\n"
                            + ocr
                            + "\n'''"
                            + postcursor
                        )
                        img_msg["format"] = "description"

                    except ImportError:
                        print(
                            "\nTo use local vision, run `pip install 'open-interpreter[local]'`.\n"
                        )
                        img_msg["format"] = "description"
                        img_msg["content"] = ""


        # --- normalize + trim (text) BEFORE Responses conversion ----------------------
        # Ensure every item has a 'type'
        messages = [msg if "type" in msg else {**msg, "type": "message"} for msg in messages]

        if (
            self.previous_response_id
            and self.previous_response_message_count is not None
            and len(messages) > 1
        ):
            system_msg = messages[0]
            conversation_messages = messages[1:]
            recent_messages = conversation_messages[self.previous_response_message_count :]
            if recent_messages:
                messages = [system_msg] + recent_messages

        # 1) Pull out the system message (string) for trimming.
        assert messages and messages[0].get("role") == "system", "First message must be system"
        raw_system_message = messages[0].get("content", "") or ""
        if not isinstance(raw_system_message, str):
            raw_system_message = str(raw_system_message)

        # 2) Split the rest into text-vs-nontext so tokentrim only sees text.
        text_msgs = [m for m in messages[1:] if m.get("type") == "message"]
        other_msgs = [m for m in messages[1:] if m.get("type") != "message"]

        # 3) Run tokentrim on text-only messages.
        try:
            if self.context_window and self.max_tokens:
                trim_to = self.context_window - self.max_tokens - 25  # small buffer
                trimmed_text = tt.trim(
                    text_msgs,
                    system_message=raw_system_message,
                    max_tokens=trim_to,
                )
            elif self.context_window and not self.max_tokens:
                trimmed_text = tt.trim(
                    text_msgs,
                    system_message=raw_system_message,
                    max_tokens=self.context_window,
                )
            else:
                try:
                    trimmed_text = tt.trim(
                        text_msgs, system_message=raw_system_message, model=model
                    )
                except:
                    trimmed_text = tt.trim(
                        text_msgs, system_message=raw_system_message, max_tokens=8000
                    )
        except Exception:
            # If trimming fails for any reason, just keep the original text messages
            trimmed_text = text_msgs

        # 4) Merge back non-text messages in original order.
        # tokentrim only drops whole text messages; it doesn’t rewrite text content,
        # so we can match on (role, content) safely.
        trim_set = {(m.get("role"), m.get("content", "")) for m in trimmed_text}
        merged_msgs = []
        for m in messages[1:]:
            if m.get("type") != "message":
                merged_msgs.append(m)
            else:
                if (m.get("role"), m.get("content", "")) in trim_set:
                    merged_msgs.append(m)

        # 5) Rebuild a linear list with the system back at the front (still plain, not parts).
        messages_for_conversion = (
            [{"role": "system", "type": "message", "content": raw_system_message}] + merged_msgs
        )

        # 6) Convert to Responses-style items (images -> input_image data URLs, etc.)
        messages = convert_to_openai_responses_messages(
            messages_for_conversion,
            shrink_images=self.interpreter.shrink_images,
            interpreter=self.interpreter,
        )

        # 7) Pop system into Responses 'instructions' (string), keep the rest as input items.
        instructions_parts = []
        if messages and messages[0].get("role") == "system":
            instructions_parts = messages[0].get("content", [])
            messages = messages[1:]

        def _parts_to_text(parts):
            return "".join(
                p.get("text", "")
                for p in (parts or [])
                if isinstance(p, dict) and p.get("type") in ("input_text", "output_text", "text")
            )

        system_message = _parts_to_text(instructions_parts)

        # 8) Final safety normalize (no tool_calls/function_call fields, correct part types).
        messages = [self._ensure_responses_message_shape(m) for m in messages]
        # --- end: normalize + trim + convert -----------------------------------------

        #print("DEBUG first input item:", messages[0])

        # Responses parameters
        params = {
            "model": model,
            "instructions": system_message,  # system goes here in Responses
            "input": messages,               # your chat-style items (role/content) go here
            "stream": True,
            "_interpreter_llm": self,
        }

        def _allows_sampling_knobs(model: str) -> bool:
            # Reasoning/Responses models typically disallow temperature/top_p/etc.
            return not (model.startswith(("gpt-5", "o4", "o3")))

        # Optional inputs
        if self.api_key:
            params["api_key"] = self.api_key
        if self.api_base:
            params["api_base"] = self.api_base
        if self.api_version:
            params["api_version"] = self.api_version
        if self.max_tokens:
            #params["max_tokens"] = self.max_tokens # Chat completions param
            params["max_output_tokens"] = self.max_tokens # Responses param      
        if self.temperature is not None and _allows_sampling_knobs(model):
            # Only add temperature if the model allows it
            params["temperature"] = self.temperature
        if hasattr(self.interpreter, "conversation_id"):
            params["conversation_id"] = self.interpreter.conversation_id
        if self.previous_response_id:
            params["previous_response_id"] = self.previous_response_id

        # Forward reasoning effort (Responses API expects `reasoning={"effort": ...}`)
        if self.reasoning_effort:
            # If you used old values like "low"/"high", just set self.reasoning_effort
            # to the new ones you want (e.g., "minimal", "medium", "intense") before calling run().
            params["reasoning"] = {"effort": self.reasoning_effort}
        allowed_openai_params = []
        if self.reasoning_effort:
            allowed_openai_params.append("reasoning")
        if self.previous_response_id:
            allowed_openai_params.append("previous_response_id")
        if allowed_openai_params:
            params["allowed_openai_params"] = allowed_openai_params


        # # Debug print params summary
        # def _summarize_messages(msgs, n=2):
        #     try:
        #         return [{"role": m.get("role"), "content_type": type(m.get("content")).__name__} for m in msgs[:n]]
        #     except Exception as e:
        #         return f"<summarize error: {e}>"

        # print("[run] params summary:",
        #     {
        #         "model": params.get("model"),
        #         "has_api_key": "api_key" in params,
        #         "stream": params.get("stream"),
        #         "num_input_items": len(params.get("input", [])),
        #         "instructions_len": len(params.get("instructions", "")) if isinstance(params.get("instructions"), str) else "n/a",
        #         "input_preview": _summarize_messages(params.get("input", []))
        #     },
        #     flush=True)
        # # End debug print

        # Set some params directly on LiteLLM
        if self.max_budget:
            litellm.max_budget = self.max_budget
        if self.interpreter.verbose:
            litellm.set_verbose = True

        if (
            self.interpreter.debug == True and False  # DISABLED
        ):  # debug will equal "server" if we're debugging the server specifically
            print("\n\n\nOPENAI COMPATIBLE MESSAGES:\n\n\n")
            for message in messages:
                if len(str(message)) > 5000:
                    print(str(message)[:200] + "...")
                else:
                    print(message)
                print("\n")
            print("\n\n\n")

        if self.supports_functions:
            # yield from run_function_calling_llm(self, params)
            yield from run_tool_calling_llm(self, params)
        else:
            yield from run_text_llm(self, params)

    # If you change model, set _is_loaded to false
    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, value):
        self._model = value
        self._is_loaded = False

    def load(self):
        if self._is_loaded:
            return

        if self.model.startswith("ollama/") and not ":" in self.model:
            self.model = self.model + ":latest"

        self._is_loaded = True

        if self.model.startswith("ollama/"):
            model_name = self.model.replace("ollama/", "")
            api_base = getattr(self, "api_base", None) or os.getenv(
                "OLLAMA_HOST", "http://localhost:11434"
            )
            names = []
            try:
                # List out all downloaded ollama models. Will fail if ollama isn't installed
                response = requests.get(f"{api_base}/api/tags")
                if response.ok:
                    data = response.json()
                    names = [
                        model["name"]
                        for model in data["models"]
                        if "name" in model and model["name"]
                    ]

            except Exception as e:
                print(str(e))
                self.interpreter.display_message(
                    f"> Ollama not found\n\nPlease download Ollama from [ollama.com](https://ollama.com/) to use `{model_name}`.\n"
                )
                exit()

            # Download model if not already installed
            if model_name not in names:
                self.interpreter.display_message(f"\nDownloading {model_name}...\n")
                requests.post(f"{api_base}/api/pull", json={"name": model_name})

            # Get context window if not set
            if self.context_window == None:
                response = requests.post(
                    f"{api_base}/api/show", json={"name": model_name}
                )
                model_info = response.json().get("model_info", {})
                context_length = None
                for key in model_info:
                    if "context_length" in key:
                        context_length = model_info[key]
                        break
                if context_length is not None:
                    self.context_window = context_length
            if self.max_tokens == None:
                if self.context_window != None:
                    self.max_tokens = int(self.context_window * 0.2)

            # Send a ping, which will actually load the model
            model_name = model_name.replace(":latest", "")
            print(f"Loading {model_name}...\n")

            old_max_tokens = self.max_tokens
            self.max_tokens = 1
            self.interpreter.computer.ai.chat("ping")
            self.max_tokens = old_max_tokens

            self.interpreter.display_message("*Model loaded.*\n")

        # Validate LLM should be moved here!!

        if self.context_window == None:
            try:
                model_info = litellm.get_model_info(model=self.model)
                self.context_window = model_info["max_input_tokens"]
                if self.max_tokens == None:
                    self.max_tokens = min(
                        int(self.context_window * 0.2), model_info["max_output_tokens"]
                    )
            except:
                pass

def _responses_events_to_chat_deltas(events_iter, llm=None):
    """Adapt Responses events to Chat-like deltas (incl. function-call streaming)."""
    sent_role = False

    # Track in-flight function calls by item_id
    func_calls = {}  # item_id -> {"name": str|None, "args": str}
    pending_response_id = None

    def _etype(ev):
        """
        Return the canonical lower-case event type string like
        'response.output_text.delta'. Handles enum, dict, or string types.
        """
        t = getattr(ev, "type", None)
        if t is not None and hasattr(t, "value"):  # Enum case
            return str(t.value).lower()
        if isinstance(ev, dict):                  # Dict payload
            return str(ev.get("type", "")).lower()
        return (str(t) if t else "").lower()      # Fallback

    def _get(obj, key, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    for ev in events_iter:
        # print("[events] ev:", ev, flush=True)
        t = _etype(ev)

        response = _get(ev, "response")
        response_id = _get(response, "id") or _get(ev, "response_id")
        if response_id:
            pending_response_id = response_id

        # Emit assistant role once
        if not sent_role and t.startswith("response."):
            sent_role = True
            yield {"choices": [{"delta": {"role": "assistant"}}]}

        # ── Plain text streaming ────────────────────────────────────────────────
        if t == "response.output_text.delta":
            chunk = _get(ev, "delta") or _get(ev, "text") or ""
            if chunk:
                yield {"choices": [{"delta": {"content": chunk}}]}
            continue
        if t in ("response.output_text.done", "response.content_part.done"):
            continue  # nothing to emit

        # ── Function-call lifecycle ────────────────────────────────────────────
        if t == "response.output_item.added":
            item = _get(ev, "item")
            if _get(item, "type") == "function_call":
                fid = _get(item, "id")
                call_id = _get(item, "call_id") or fid
                name = _get(item, "name")
                if fid:
                    func_calls[fid] = {"name": name, "args": "", "call_id": call_id}
                    # Announce start with empty args so downstream knows the name
                    yield {
                        "choices": [{
                            "delta": {
                                "tool_calls": [{
                                    "id": call_id,
                                    "function": {"name": name or "execute", "arguments": ""}
                                }]
                            }
                        }]
                    }
            continue

        # Incremental JSON argument text
        if t == "response.function_call_arguments.delta":
            fid = _get(ev, "item_id")
            piece = _get(ev, "delta", "")
            state = func_calls.setdefault(fid or "<unknown>", {"name": None, "args": "", "call_id": fid})
            state["args"] += piece or ""
            # Emit only the NEW piece (Chat Completions semantics)
            yield {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "function": {
                                "name": state["name"] or "execute",
                                "arguments": piece or "",
                            }
                        }]
                    }
                }]
            }
            continue

        # Do NOT emit on .done — just store final string (avoids duplicate append)
        if t == "response.function_call_arguments.done":
            fid = _get(ev, "item_id")
            final_args = _get(ev, "arguments", "")
            state = func_calls.setdefault(fid or "<unknown>", {"name": None, "args": "", "call_id": fid})
            if isinstance(final_args, str) and len(final_args) >= len(state["args"]):
                state["args"] = final_args
            continue  # <-- no yield here

        # Also DO NOT emit again when the function_call item completes
        if t == "response.output_item.done":
            item = _get(ev, "item")
            if _get(item, "type") == "function_call":
                # We've already streamed the incremental arguments pieces.
                # Emitting again here would double-append. Do nothing.
                pass
            continue

        # ── Completion / error ─────────────────────────────────────────────────
        if (t == "response.completed") or t.endswith("response_completed"):
            if pending_response_id and llm is not None:
                llm.previous_response_id = pending_response_id
                llm.previous_response_message_count = len(llm.interpreter.messages)
            yield {"choices": [{"finish_reason": "stop"}]}
            break
        if (t == "response.error") or t.endswith("response_error"):
            yield {"choices": [{"finish_reason": "error"}]}
            break

        # Any other event types are ignored
        continue

def fixed_litellm_completions(**params):
    """
    LiteLLM wrapper for both Chat Completions (old) and Responses (new).
    This version includes a small validator for Responses input items and
    never leaks Chat Completions-only fields into Responses.
    """
    llm = params.pop("_interpreter_llm", None)

    if "local" in params.get("model", ""):
        params["stop"] = ["<|assistant|>", "<|end|>", "<|eot_id|>"]

    if params.get("model") == "i" and "conversation_id" in params:
        litellm.drop_params = False
    else:
        litellm.drop_params = True

    params["model"] = params["model"].replace(":latest", "")
    params["num_retries"] = 0

    # --- DEBUG/SAFETY: validate Responses shape before calling
    def _validate_responses_input(items):
        for idx, it in enumerate(items or []):
            if it.get("type") == "function_call_output":
                continue
            c = it.get("content")
            if not isinstance(c, list):
                print(f"[VALIDATE] input[{idx}] content must be a list; got: {type(c).__name__}")
            bad = [k for k in ("tool_calls", "function_call", "tool_call_id") if k in it]
            if bad:
                print(f"[VALIDATE] input[{idx}] has forbidden keys: {bad}")

    attempts = 4
    first_error = None

    for attempt in range(attempts):
        try:
            if "input" in params and isinstance(params["input"], list):
                _validate_responses_input(params["input"])

            # Responses path
            if "input" in params or "instructions" in params:
                print("[responses] calling litellm.responses with keys:",
                      list(params.keys()), flush=True)
                events = litellm.responses(**params)
                print("[responses] received events:", events, flush=True)
                for delta in _responses_events_to_chat_deltas(events, llm=llm):
                    yield delta
            else:
                # Chat Completions path (legacy)
                yield from litellm.completion(**params)

            return
        except KeyboardInterrupt:
            print("Exiting...")
            sys.exit(0)
        except Exception as e:
            print(f"[responses attempt {attempt}] exception: {type(e).__name__}: {e}", flush=True)
            if attempt == 0:
                first_error = e
            if isinstance(e, litellm.exceptions.AuthenticationError) and "api_key" not in params:
                print("LiteLLM requires an API key. Retrying with a dummy key.")
                params["api_key"] = "x"

    if first_error is not None:
        raise first_error
