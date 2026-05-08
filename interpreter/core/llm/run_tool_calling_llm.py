from builtins import print
import os
import re

from .utils.merge_deltas import merge_deltas
from .utils.parse_partial_json import parse_partial_json

# Responses-compliant tool schema (top-level name)
tool_schema = {
    "type": "function",
    "name": "execute",
    "description": "Executes code on the user's machine **in the users local environment** and returns the output",
    "parameters": {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "description": "The programming language (required parameter to the `execute` function)",
                "enum": [],  # filled dynamically in run_tool_calling_llm
            },
            "code": {
                "type": "string",
                "description": "The code to execute (required)",
            },
        },
        "required": ["language", "code"],
    },
}

def process_messages(messages):
    """
    Normalize conversation for the chosen API route.

    For Responses:
      - Each item -> {role, content=[{type: input_text/output_text, text: ...}]}
      - Strip Chat Completions metadata
      - Map any role 'tool'/'function' to 'user'
      - Do NOT synthesize assistant items with tool_calls
    For legacy Chat Completions:
      - Keep previous behavior (tool_calls etc.)
    """
    processed = []
    last_tool_id = 0

    def _is_responses_item(m):
        if m.get("type") == "function_call_output":
            return True
        c = m.get("content")
        return isinstance(c, list) and (len(c) == 0 or isinstance(c[0], dict))

    responses_mode = any(isinstance(m, dict) and _is_responses_item(m) for m in messages or [])

    def _empty_content_for_role(role: str):
        if responses_mode:
            part_type = "output_text" if role == "assistant" else "input_text"
            return [{"type": part_type, "text": ""}]
        return ""

    def _force_parts_list(role: str, content):
        if isinstance(content, list):
            return content
        text = content if isinstance(content, str) else str(content or "")
        part_type = "output_text" if role == "assistant" else "input_text"
        return [{"type": part_type, "text": text}]

    def _strip_chat_completions_metadata(m: dict) -> dict:
        for k in ("tool_calls", "function_call", "tool_call_id", "name"):
            if k in m:
                m.pop(k, None)
        return m

    i = 0
    while i < len(messages):
        msg = messages[i]
        msg = msg.copy() if isinstance(msg, dict) else {"role": "user", "content": str(msg)}
        role = msg.get("role", "user")

        if responses_mode and msg.get("type") == "compaction":
            processed.append(
                {
                    key: msg[key]
                    for key in ("id", "encrypted_content", "type")
                    if key in msg and msg[key] is not None
                }
            )
            i += 1
            continue

        if responses_mode and msg.get("type") == "function_call_output":
            processed.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.get("call_id"),
                    "output": msg.get("output", ""),
                }
            )
            i += 1
            continue

        if "content" not in msg:
            msg["content"] = _empty_content_for_role(role)

        if responses_mode:
            # STRICT Responses normalization
            msg = _strip_chat_completions_metadata(msg)

            # Map legacy roles to 'user'
            role = msg.get("role", "user")
            if role in ("tool", "function"):
                role = "user"
            if role not in ("user", "assistant", "system", "developer"):
                role = "user"
            msg["role"] = role

            msg["content"] = _force_parts_list(role, msg.get("content", ""))
            processed.append(msg)
            i += 1
            continue

        # ----- Legacy Chat Completions path (unchanged) -----
        if msg.get("function_call"):
            last_tool_id += 1
            tool_id = f"toolu_{last_tool_id}"

            function = msg.pop("function_call")
            msg.setdefault("content", "")
            msg.setdefault("tool_calls", [])
            msg["tool_calls"].append({"id": tool_id, "type": "function", "function": function})
            processed.append(msg)

            # If next message is a function response, convert to tool
            if i + 1 < len(messages) and messages[i + 1].get("role") == "function":
                next_msg = messages[i + 1].copy()
                next_msg["role"] = "tool"
                next_msg["tool_call_id"] = tool_id
                if "content" not in next_msg:
                    next_msg["content"] = ""
                processed.append(next_msg)
                i += 1
            else:
                processed.append({"role": "tool", "tool_call_id": tool_id, "content": ""})

        elif msg.get("role") == "function":
            last_tool_id += 1
            tool_id = f"toolu_{last_tool_id}"

            processed.append({
                "role": "assistant",
                "tool_calls": [{
                    "id": tool_id,
                    "type": "function",
                    "function": {
                        "name": "execute",
                        "arguments": "# Automated tool call to fetch more output, triggered by the user.",
                    },
                }],
            })
            msg["role"] = "tool"
            msg["tool_call_id"] = tool_id
            if "content" not in msg:
                msg["content"] = ""
            processed.append(msg)

        else:
            if "content" not in msg:
                msg["content"] = ""
            processed.append(msg)

        i += 1

    return processed


def run_tool_calling_llm(llm, request_params):
    def _extract_payload(txt: str):
        stripped = (txt or "").strip()

        # Prefer a fenced JSON block if present
        fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", stripped)
        if fence:
            candidate = fence.group(1).strip()
        else:
            # Find the first balanced JSON object, allowing surrounding prose
            start = stripped.find("{")
            if start == -1:
                return None
            depth = 0
            in_str = False
            esc = False
            end = None
            for i, ch in enumerate(stripped[start:], start=start):
                if in_str:
                    if ch == "\\":
                        esc = not esc
                    elif ch == '"' and not esc:
                        in_str = False
                    else:
                        esc = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
            if end is None:
                return None
            candidate = stripped[start : end + 1]

        parsed = parse_partial_json(candidate)
        if isinstance(parsed, dict) and parsed.get("code") is not None:
            lang = parsed.get("language") if isinstance(parsed.get("language"), str) else None
            return (lang or "python").strip(), parsed["code"]

        return None

    # 1) Fill tool schema languages (once per call)
    tool_schema["parameters"]["properties"]["language"]["enum"] = [
        i.name.lower() for i in llm.interpreter.computer.terminal.languages
    ]

    # 2) Decide route & normalize messages
    # Prefer Responses "input"; fall back to legacy "messages"
    if "input" in request_params:
        msgs = request_params["input"]
        using_responses = True
    elif "messages" in request_params:
        msgs = request_params["messages"]
        using_responses = False
    else:
        msgs = []
        using_responses = True  # default to Responses

    msgs = process_messages(msgs)

    # 3) Build Responses params
    request_params["input"] = msgs
    if "messages" in request_params:
        del request_params["messages"]

    # Only include tools for models that support functions
    if llm.supports_functions:
        request_params["tools"] = [tool_schema]
        request_params.setdefault("tool_choice", "auto")
    else:
        request_params.pop("tools", None)
        request_params.pop("tool_choice", None)

    # 4) Stream back to the caller (unchanged logic, but now safe shapes)
    accumulated_deltas = {}
    language = None
    code = ""
    function_call_detected = False
    accumulated_review = ""
    review_category = None
    buffer = ""
    assistant_text = ""
    saw_code_argument = False
    emitted_code_chunk = False

    for chunk in llm.completions(**request_params):
        # If this is a Responses adapter delta without 'choices', skip
        if "choices" not in chunk or not chunk["choices"]:
            continue

        choice0 = chunk["choices"][0]
        delta = choice0.get("delta")
        if delta is None:
            finish = choice0.get("finish_reason")
            if finish == "stop":
                break
            if finish == "error":
                raise RuntimeError("Model returned an error finish.")
            continue

        # Chat Completions-style tool_calls → function_call (kept for judge/code streaming)
        if "tool_calls" in delta and delta["tool_calls"]:
            function_call_detected = True
            tc0 = delta["tool_calls"][0]
            call_id = getattr(tc0, "id", None) or (tc0.get("id") if isinstance(tc0, dict) else None)
            fn = getattr(tc0, "function", None) or (tc0.get("function") if isinstance(tc0, dict) else None)
            if fn:
                name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else None)
                args = getattr(fn, "arguments", None) or (fn.get("arguments") if isinstance(fn, dict) else "")
                if not isinstance(args, str):
                    try:
                        import json
                        args = json.dumps(args, ensure_ascii=False)
                    except Exception:
                        args = str(args)
                new_delta = {k: v for k, v in delta.items() if k != "tool_calls"}
                new_delta["function_call"] = {"name": name, "arguments": args, "call_id": call_id}
                delta = new_delta

        # Accumulate for code / review handling
        accumulated_deltas = merge_deltas(accumulated_deltas, delta)

        # Stream assistant text / judge output
        if "content" in delta and delta["content"]:
            if not function_call_detected:
                assistant_text += delta["content"]
            if function_call_detected:
                if review_category is None:
                    accumulated_review += delta["content"]
                    if "<unsafe>" in accumulated_review:
                        review_category = "unsafe"
                    if "<warning>" in accumulated_review:
                        review_category = "warning"
                    if "<safe>" in accumulated_review:
                        review_category = "safe"
                if review_category is not None:
                    for tag in ["<safe>", "</safe>", "<warning>", "</warning>", "<unsafe>", "</unsafe>"]:
                        delta["content"] = delta["content"].replace(tag, "")
                    if re.search("</.*>$", accumulated_review):
                        buffer += delta["content"]
                        continue
                    elif buffer:
                        yield {"type": "review", "format": review_category, "content": buffer + delta["content"]}
                        buffer = ""
                    else:
                        yield {"type": "review", "format": review_category, "content": delta["content"]}
                        buffer = ""
            else:
                yield {"type": "message", "content": delta["content"]}

        # Stream incremental code (parse partial JSON arguments)
        if accumulated_deltas.get("function_call") and accumulated_deltas["function_call"].get("arguments"):
            arguments_text = accumulated_deltas["function_call"]["arguments"]
            arguments = parse_partial_json(arguments_text)
            if arguments and "code" in arguments:
                saw_code_argument = True
                if language is None:
                    language = arguments.get("language") or "python"
                    call_id = accumulated_deltas["function_call"].get("call_id")
                    if call_id:
                        llm.pending_tool_call_id = call_id
                code_delta = arguments["code"][len(code):]
                code = arguments["code"]
                if code_delta:
                    chunk = {"type": "code", "format": language, "content": code_delta}
                    call_id = accumulated_deltas["function_call"].get("call_id")
                    if call_id:
                        chunk["call_id"] = call_id
                    emitted_code_chunk = True
                    yield chunk
            else:
                if llm.interpreter.verbose:
                    print("Arguments not a dict or no 'code' yet.")

    if function_call_detected and saw_code_argument and not emitted_code_chunk:
        chunk = {"type": "code", "format": language or "python", "content": ""}
        call_id = accumulated_deltas.get("function_call", {}).get("call_id")
        if call_id:
            chunk["call_id"] = call_id
        yield chunk

    # Fallback: if the model never produced a tool call but returned a pure code payload, execute it.
    if not function_call_detected and assistant_text.strip():
        payload = _extract_payload(assistant_text)
        if payload:
            lang, extracted_code = payload
            yield {"type": "code", "format": lang or "python", "content": extracted_code}

    if os.getenv("INTERPRETER_REQUIRE_AUTHENTICATION", "False").lower() == "true":
        if function_call_detected and not accumulated_review:
            raise Exception("Judge layer required but did not run.")
