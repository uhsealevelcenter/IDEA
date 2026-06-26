# OpenInterpreter — How It Works Internally

A deep-dive into how the library reasons, plans, executes code, and loops back to the LLM.

---

## Table of Contents

1. [High-Level Architecture](#1-high-level-architecture)
2. [Entry Point — `OpenInterpreter` Class](#2-entry-point--openinterpreter-class)
3. [Message Format — LMC Messages](#3-message-format--lmc-messages)
4. [The System Prompt — How the LLM Is Primed](#4-the-system-prompt--how-the-llm-is-primed)
5. [The Chat Flow](#5-the-chat-flow)
6. [The Core Respond Loop](#6-the-core-respond-loop)
7. [LLM Layer — How Completion Requests Are Built](#7-llm-layer--how-completion-requests-are-built)
8. [Two Execution Modes: Tool-Calling vs. Text LLM](#8-two-execution-modes-tool-calling-vs-text-llm)
9. [Code Execution — The Computer](#9-code-execution--the-computer)
10. [Streaming & Message Assembly](#10-streaming--message-assembly)
11. [Loop Mode — Autonomous Task Completion](#11-loop-mode--autonomous-task-completion)
12. [OS Control Mode](#12-os-control-mode)
13. [Context Window Management](#13-context-window-management)
14. [Full Data Flow Diagram](#14-full-data-flow-diagram)
15. [IDEA Integration — Endpoint & Request Flow](#15-idea-integration--endpoint--request-flow)
16. [Long Conversations — Full Context vs. Trimmed Slices](#16-long-conversations--full-context-vs-trimmed-slices)

---

## 1. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         OpenInterpreter                          │
│                                                                  │
│   ┌─────────┐    ┌──────────────────┐    ┌───────────────────┐  │
│   │  User   │───▶│  chat() entry    │───▶│  respond() loop   │  │
│   │ Message │    │  point           │    │  (core engine)    │  │
│   └─────────┘    └──────────────────┘    └────────┬──────────┘  │
│                                                   │              │
│                          ┌────────────────────────┤              │
│                          ▼                        ▼              │
│                    ┌──────────┐           ┌──────────────┐       │
│                    │   Llm    │           │   Computer   │       │
│                    │ (LiteLLM)│           │  (Terminal)  │       │
│                    └──────────┘           └──────────────┘       │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

The three pillars of the library:

- **`OpenInterpreter`** (`core/core.py`) — orchestrator / "grand central station"
- **`Llm`** (`core/llm/llm.py`) — everything about sending messages to the model and parsing responses
- **`Computer`** (`core/computer/computer.py`) — everything about running code and interacting with the OS

---

## 2. Entry Point — `OpenInterpreter` Class

**File:** `interpreter/core/core.py`

The class is described as the "grand central station." Its responsibilities in order are:

1. Given user input, **prompt the language model**.
2. **Parse the LLM's response** into LMC Messages.
3. **Send code to the computer** for execution.
4. **Parse the computer's output** back into LMC Messages.
5. **Send the computer's output back to the LLM**.
6. **Repeat** until the LLM decides it's done (no more code to run).

### Key instance attributes

| Attribute | Purpose |
|---|---|
| `self.messages` | Full conversation history (list of LMC dicts) |
| `self.llm` | `Llm` instance that wraps LiteLLM |
| `self.computer` | `Computer` instance providing code execution + OS tools |
| `self.system_message` | Base system prompt (primes the LLM's persona/behavior) |
| `self.custom_instructions` | Appended to system message at runtime |
| `self.auto_run` | If `True`, skip the user confirmation step before running code |
| `self.loop` | If `True`, auto-inject "Proceed" messages to keep the task going |
| `self.loop_message` | The exact text injected to push the LLM to continue |
| `self.loop_breakers` | Phrases that, if spoken by the LLM, will stop the loop |
| `self.max_output` | Truncation limit for code output (default 2800 chars) |
| `self.os` | Enables OS Control mode (screenshots, mouse, keyboard) |

---

## 3. Message Format — LMC Messages

OpenInterpreter uses its own internal message format called **LMC (Language Model Conversation)** rather than the raw OpenAI format.

Every message is a dict with these fields:

```python
{
    "role":    "user" | "assistant" | "computer" | "system",
    "type":    "message" | "code" | "console" | "image" | "confirmation" | "review",
    "format":  "output" | "active_line" | "python" | "shell" | "javascript" | ...,
    "content": "...",
    # Optional:
    "call_id": "tool-call-uuid",   # links code block to its output
}
```

### Roles and what they mean

| Role | Produced by | Meaning |
|---|---|---|
| `user` | Human | Human input or code output feedback |
| `assistant` | LLM | LLM's message text or code block |
| `computer` | Terminal | Execution output, active line indicator, or confirmation request |
| `system` | Core | System prompt (only at position 0, never stored in `self.messages`) |

### Key type/format combinations

| type | format | Meaning |
|---|---|---|
| `message` | _(none)_ | Plain natural language text |
| `code` | `python`, `shell`, `javascript`, … | A code block the LLM wants to run |
| `console` | `output` | Text output from running code |
| `console` | `active_line` | Which line of code is currently executing (for UI highlighting) |
| `image` | `path`, `base64`, `description` | Screenshot or image |
| `confirmation` | `execution` | "Are you sure you want to run this code?" gate |

---

## 4. The System Prompt — How the LLM Is Primed

**File:** `interpreter/core/default_system_message.py`

```
You are Open Interpreter, a world-class programmer that can complete any goal by executing code.
For advanced requests, start by writing a plan.
When you execute code, it will be executed **on the user's machine**. The user has given you **full
and complete permission** to execute any code necessary to complete the task. Execute the code.
You can access the internet. Run **any code** to achieve the goal, and if at first you don't succeed,
try again and again.
You can install new packages.
...
In general, try to **make plans** with as few steps as possible. For *stateful* languages (like python,
javascript, shell, but NOT for html) **it's critical not to try to do everything in one code block.**
You should try something, print information about it, then continue from there in tiny, informed steps.
...
User's Name: {getpass.getuser()}
User's OS: {platform.system()}
```

### System message assembly (at each LLM call)

The final system message sent to the LLM is assembled at runtime by `respond()`:

```
[default_system_message]
  + [language-specific system messages, e.g. Python's hints]
  + [custom_instructions if set]
  + [computer API docs if import_computer_api=True]
```

The system message also supports **dynamic rendering** via `render_message()`. Any `{{ python_code }}` block inside the system message is executed at call time, and its output is injected inline. This lets the system message include live state (e.g., current directory, installed packages).

### Execution instructions (text-only models)

For models that don't support tool/function calling, a fallback instruction is appended:

> "To execute code on the user's machine, write a markdown code block. Specify the language after the \`\`\`. You will receive the output."

---

## 5. The Chat Flow

```
interpreter.chat("do something")
        │
        ▼
chat()  ─── non-blocking? ──▶ spawn thread ──▶ chat(blocking=True)
        │
        ▼  stream=True?
_streaming_chat()
        │
        ├── display=True?  ──▶ terminal_interface() [wraps with TUI]
        │
        └── display=False
                │
                ├── add message to self.messages
                ├── set self.last_messages_count
                │
                ▼
        _respond_and_store()    ◀─── main generator, yields chunks
                │
                ▼
            respond(interpreter)   ◀─── the actual loop
```

### `_respond_and_store()` responsibilities

While streaming from `respond()`, this method:

1. **Assembles LMC messages** by accumulating streamed content chunks into complete message dicts and appending to `self.messages`.
2. **Injects `call_id`** onto console output chunks so each output is linked to the code that produced it.
3. **Yields start/end flag chunks** (`{"start": True}`, `{"end": True}`) around each logical message, so consumers know message boundaries.
4. **Truncates console output** in `self.messages` when it exceeds `max_output`.
5. **Handles `GeneratorExit`** by interrupting the computer's active language runtime.

---

## 6. The Core Respond Loop

**File:** `interpreter/core/respond.py`

This is where the LLM ↔ Computer feedback loop lives.

```
while True:
    ┌─────────────────────────────────────────────────────┐
    │ 1. Build system message (with dynamic rendering)    │
    │ 2. Prepend system message to messages_for_llm       │
    │ 3. Inject loop_message if needed                    │
    └──────────────────────────┬──────────────────────────┘
                               │
                               ▼
         ┌─────────────────────────────────────────────────┐
         │ Is last message a code block?                   │
         │  NO ──▶ Call llm.run(messages_for_llm)          │
         │         Yield each chunk as role="assistant"    │
         └──────────────────────┬──────────────────────────┘
                                │
                                ▼
         ┌─────────────────────────────────────────────────┐
         │ Is last message NOW a code block?               │
         │  YES ──▶ CODE EXECUTION PATH                    │
         │   1. Parse language + code from message         │
         │   2. Yield "confirmation" chunk (user gate)     │
         │   3. Run code via computer.run(language, code)  │
         │   4. Yield each output chunk as role="computer" │
         │   5. Yield active_line=None (done signal)       │
         │  NO ──▶ LOOP / DONE CHECK                       │
         │   - loop=True & no loop_breaker? inject message │
         │   - Otherwise: break (we're done)               │
         └─────────────────────────────────────────────────┘
```

### Hallucination handling in respond()

The loop contains several guards against common LLM hallucinations:

| Hallucination | Fix |
|---|---|
| `functions.execute({"language":...,"code":...})` | Parsed and re-mapped to real language/code |
| Code block starts with `` `\n `` | Leading backtick+newline stripped |
| `executeexecute` at end of code | Stripped |
| `{"language": ..., "code": ...}` as raw JSON | Parsed as a code dispatch |
| Language is `text`, `markdown`, or `plaintext` | Re-cast as an assistant message, not executed |
| Language not supported | Yields error output, lets LLM recover |
| Empty code block | Yields error asking LLM to write code first |

---

## 7. LLM Layer — How Completion Requests Are Built

**File:** `interpreter/core/llm/llm.py`

The `Llm.run(messages)` method is responsible for:

1. **Auto-detecting capabilities** — calls `litellm.supports_function_calling(model)` and `litellm.supports_vision(model)` on first run and caches the result.
2. **Image trimming** — to save context, in OS mode only the last 2 images are kept; otherwise all but the first and last 2 are dropped.
3. **Vision fallback** — if the model doesn't support vision, images are passed through `computer.vision.query()` to get a text description, then substituted.
4. **Context trimming** — uses `tokentrim` to trim the conversation to fit within `context_window - max_tokens - 25`. Non-text messages (code, images) are preserved while text messages are dropped from the middle.
5. **Responses API conversion** — converts LMC-format messages into OpenAI Responses API shape: `{ role, content: [{type: "input_text"|"output_text", text: ...}] }`.
6. **Building params** — constructs the final `params` dict including `model`, `instructions` (system), `input` (messages), `stream=True`, plus optional `api_key`, `api_base`, `max_output_tokens`, `temperature`, `reasoning`, `previous_response_id`.
7. **Routes to correct runner** — calls `run_tool_calling_llm` or `run_text_llm` depending on `supports_functions`.

### Responses API vs. Chat Completions

The library has migrated to the **OpenAI Responses API** (`litellm.responses()`). The key differences:

| Feature | Chat Completions | Responses API |
|---|---|---|
| System | `messages[0].role = "system"` | `instructions=` parameter |
| Input | `messages=[...]` | `input=[...]` |
| Output tokens | `max_tokens=` | `max_output_tokens=` |
| Stateful continuation | Not supported | `previous_response_id=` |
| Reasoning | Not native | `reasoning={"effort": "low"\|"medium"\|"high"}` |

### `fixed_litellm_completions`

The actual LiteLLM call is wrapped in a retry loop (`attempts=4`) that:
- Detects auth errors and retries with a dummy API key for local models
- Converts the Responses streaming events into Chat-Completions-style deltas via `_responses_events_to_chat_deltas()`

---

## 8. Two Execution Modes: Tool-Calling vs. Text LLM

### Mode A: Tool-Calling LLM (`run_tool_calling_llm.py`)

Used when `llm.supports_functions = True` (e.g., GPT-4, GPT-5, Claude).

The tool schema exposed to the model is:

```json
{
  "type": "function",
  "name": "execute",
  "description": "Executes code on the user's machine in the users local environment and returns the output",
  "parameters": {
    "language": { "enum": ["python", "shell", "javascript", ...] },
    "code":     { "type": "string" }
  }
}
```

**What happens:**
1. The model streams back either a plain text response **or** a tool call with `{"language": "...", "code": "..."}`.
2. Incremental JSON argument streaming: as the arguments stream in, `parse_partial_json` decodes what's available so code can be shown to the user character-by-character.
3. If the model outputs plain text *then* a tool call, the text is yielded as a `message` chunk first.
4. A `call_id` (UUID from the tool call) is attached to the code chunk and later to its output, linking them together.
5. **Judge/review layer**: if `INTERPRETER_REQUIRE_AUTHENTICATION=true`, the model's text after a function call is inspected for `<safe>`, `<warning>`, or `<unsafe>` tags before execution is permitted.

### Mode B: Text LLM (`run_text_llm.py`)

Used when `llm.supports_functions = False` (e.g., small/local models).

The execution instruction is **appended to the system prompt**:
> "To execute code, write a markdown code block."

**What happens:**
1. The model streams raw text.
2. The parser tracks whether it's `inside_code_block` (i.e., between triple backticks).
3. The first line after ` ``` ` becomes the `language`.
4. Everything after the language line is yielded as `{"type": "code", "format": language, "content": ...}` chunks.
5. When the closing ` ``` ` is found, the function returns — the code block is complete.

---

## 9. Code Execution — The Computer

**File:** `interpreter/core/computer/computer.py`

The `Computer` class is the OS interface. It holds:

| Sub-component | Purpose |
|---|---|
| `terminal` | Runs code in Python, Shell, JS, Ruby, R, AppleScript, PowerShell, Java, React, HTML |
| `display` | Takes screenshots |
| `mouse` | Moves cursor, clicks |
| `keyboard` | Types text, presses keys |
| `clipboard` | Read/write clipboard |
| `browser` | Web search |
| `files` | File system access |
| `vision` | Image understanding / OCR |
| `ai` | Nested AI calls |
| `skills` | Loads/saves reusable Python skill functions |
| `calendar`, `mail`, `sms`, `contacts` | macOS integrations |

### `computer.run(language, code)`

This is the workhorse. Calls `terminal.run()` which:

1. Looks up or instantiates the language runtime (e.g., a persistent Python REPL).
2. Calls `language_instance.run(code)` which yields chunks.
3. Each chunk is `{"type": "console", "format": "output"|"active_line", "content": ...}`.
4. `active_line` chunks carry the currently-executing line number for IDE-style highlighting.

### Persistent language runtimes

Language instances are cached in `terminal._active_languages` dict. This means **state persists between code blocks** within one conversation — variables defined in one Python block are available in the next. This is by design: the LLM is encouraged to build up context incrementally.

### Computer API injection

When `import_computer_api=True`, before the first Python code block runs that references `computer`, this bootstrap code is silently injected:

```python
from interpreter import interpreter
computer = interpreter.computer
```

This gives the LLM direct access to mouse, keyboard, display, etc. from within its Python code.

---

## 10. Streaming & Message Assembly

The entire pipeline is **generator-based**. Nothing is buffered end-to-end; chunks flow from LiteLLM → `run_tool_calling_llm` → `respond()` → `_respond_and_store()` → `_streaming_chat()` → caller.

### Chunk lifecycle example for a code response

```
LiteLLM yields:
  {"choices":[{"delta":{"tool_calls":[{"function":{"name":"execute","arguments":"{"}}]}}]}
  {"choices":[{"delta":{"tool_calls":[{"function":{"arguments":"\"language\": \"python\", "}}]}}]}
  {"choices":[{"delta":{"tool_calls":[{"function":{"arguments":"\"code\": \"print(1)"}}]}}]}
  ...

run_tool_calling_llm yields:
  {"type": "code", "format": "python", "content": "p"}
  {"type": "code", "format": "python", "content": "r"}
  {"type": "code", "format": "python", "content": "int(1)"}
  ...

respond() yields (wrapping with role):
  {"role": "assistant", "type": "code", "format": "python", "content": "p"}
  ...
  {"role": "computer", "type": "confirmation", "format": "execution", ...}
  {"role": "computer", "type": "console", "format": "output", "content": "1\n"}
  {"role": "computer", "type": "console", "format": "active_line", "content": None}

_respond_and_store() yields:
  {"role": "assistant", "type": "code", "format": "python", "start": True}
  {"role": "assistant", "type": "code", "format": "python", "content": "print(1)"}
  {"role": "assistant", "type": "code", "format": "python", "end": True}
  {"role": "computer", "type": "console", "start": True}
  {"role": "computer", "type": "console", "format": "output", "content": "1\n"}
  {"role": "computer", "type": "console", "end": True}
```

The `start`/`end` flag chunks are **not stored** in `self.messages` — they're only for UI consumers.

---

## 11. Loop Mode — Autonomous Task Completion

When `loop=True`, the interpreter behaves like an autonomous agent. After every LLM response that **didn't run code** and doesn't contain a loop-breaker phrase, this message is injected as a user message:

```
"Proceed. You CAN run code on my machine. If the entire task I asked for is done, say exactly
'The task is done.' If you need some specific information (like username or password) say EXACTLY
'Please provide more information.' If it's impossible, say 'The task is impossible.'
Otherwise keep going."
```

The loop **terminates** when the LLM says one of:
- `"The task is done."`
- `"The task is impossible."`
- `"Let me know what you'd like to do next."`
- `"Please provide more information."`

Before injecting the loop message, the code **combines adjacent assistant messages** and removes any prior loop message injections — this teaches the model to "just keep going" rather than waiting.

---

## 12. OS Control Mode

When `os=True`, the interpreter is in **computer-use** mode:
- The LLM can take screenshots and use them to understand the current state of the screen.
- Older screenshots are aggressively pruned (only the last 2 are kept) to save context.
- The loop message is modified to add: *"If the entire task I asked for is done, take a screenshot to verify it's complete..."*
- The computer API (mouse, keyboard, display) is the primary action surface.

---

## 13. Context Window Management

The `Llm.run()` method trims the conversation before every LLM call:

```
tokentrim separates text messages from non-text (code/image)
         │
         ▼
text messages trimmed to fit: context_window - max_tokens - 25
         │
         ▼
non-text messages reinserted at their original positions
         │
         ▼
system message (now called "instructions") extracted from the list
         │
         ▼
Responses API call with instructions= and input=
```

If `previous_response_id` is set (stateful Responses API continuation), only the **delta since the last call** is sent, rather than replaying the full conversation.

---

## 14. Full Data Flow Diagram

```
User Input (string / dict / list)
         │
         ▼
chat() ──┬── non-blocking ──▶ Thread
         └── blocking
                  │
                  ▼
         _streaming_chat()
                  │
           append to self.messages
                  │
                  ▼
         _respond_and_store()  [assembles messages, yields start/end flags]
                  │
                  ▼
         ┌── respond(interpreter) ──────────────────────────────────────┐
         │                                                              │
         │  Build system_message                                        │
         │    = default + language hints + custom_instructions          │
         │    + computer_api docs                                       │
         │    + dynamic {{ code }} rendering                            │
         │                                                              │
         │  messages_for_llm = [system] + self.messages                 │
         │  (+ loop_message if in loop mode)                            │
         │                                                              │
         │  LLM call (if last message is not already code):             │
         │    llm.run(messages_for_llm)                                 │
         │      → context trim (tokentrim)                              │
         │      → image trim / vision fallback                          │
         │      → convert to Responses API format                       │
         │      → litellm.responses() or litellm.completion()           │
         │      → _responses_events_to_chat_deltas() adapter            │
         │      → run_tool_calling_llm OR run_text_llm                  │
         │           ↓ yields {"type":"message"} or {"type":"code"}     │
         │                                                              │
         │  If last message is code:                                    │
         │    Yield confirmation chunk (user can stop here)             │
         │    computer.run(language, code)  [persistent REPL]          │
         │      → Terminal._streaming_run()                             │
         │      → language_instance.run(code)                          │
         │      → yields active_line + output chunks                   │
         │                                                              │
         │  If last message is NOT code:                                │
         │    loop=True and no loop_breaker? → inject loop_message      │
         │    Otherwise → break (done)                                  │
         │                                                              │
         └──────────────────────────────────────────────────────────────┘
                  │
                  ▼
         Save conversation to JSON (if conversation_history=True)
                  │
                  ▼
         Return self.messages[last_messages_count:]
```

---

## 15. IDEA Integration — Endpoint & Request Flow

### Exposed endpoint

```
POST /idea-api/chat
```

Defined in `routes/chat.py`, registered in `app.py` with `root_path="/idea-api"`. It is rate-limited at **10 requests / minute** per IP via `slowapi`.

### Required headers

| Header | Purpose |
|---|---|
| `Authorization: Bearer <token>` | JWT auth, validated by `get_current_user()` |
| `x-session-id` | Caller-supplied UUID that namespaces the interpreter instance |

### Session keying

Every interpreter is stored in a process-local dict `interpreter_instances` keyed by `"{user_id}:{session_id}"` (formed in `backend/state.py` via `make_session_key()`). This means two browser tabs with the same `session_id` for the same user share one interpreter; different session IDs get independent interpreters.

### Request flow, step by step

```
Client
  │  POST /idea-api/chat
  │  headers: Authorization, x-session-id
  │  body:    { "messages": [...full UI history...] }
  │
  ▼
FastAPI chat_endpoint()          (routes/chat.py)
  │
  ├─ authenticate JWT → user object
  ├─ build session_key = f"{user.id}:{session_id}"
  ├─ get_or_create_interpreter(session_key)   ← in-memory dict lookup / new
  │     Sets: system_message, model, context_window (400k), max_output (64k),
  │           auto_run=True, custom_tool pre-loaded into Python REPL
  │
  ├─ custom_instructions = get_custom_instructions(...)   ← injected each call
  │     Contains: host, user_id, session_id, upload paths,
  │               available custom functions (get_datetime, get_station_info, etc.)
  │               and any MCP tool descriptions
  │
  ├─ Redis GET "messages:{session_key}" → interpreter.messages
  │     (Full prior conversation is RESTORED into the interpreter object)
  │
  ├─ plan_and_run_mcp_tools(last_user_message)   ← optional pre-flight
  │
  └─ StreamingResponse( event_stream() )
           │
           ├─ [stream MCP tool status chunks if any]
           │
           ├─ interpreter.chat(messages[-1], stream=True)
           │       ↑ Only the LAST message from the body is handed to OI.
           │       ↑ OI will append it to interpreter.messages internally.
           │
           │    [yields SSE chunks: role/type/content dicts]
           │
           └─ Redis SET "messages:{session_key}" ← interpreter.messages
                   (Full updated conversation saved back to Redis)
```

### Key detail: only `messages[-1]` is passed to `interpreter.chat()`

The request body carries the complete UI-side message list, but the endpoint extracts only the **last element** and passes it to OI:

```python
# routes/chat.py:202
for result in interpreter.chat(messages[-1], stream=True):
```

The prior turn history is not fed from the request body — it is loaded from Redis into `interpreter.messages` *before* the call. OI's `_streaming_chat()` then appends the new user message to `self.messages` and the respond loop builds `messages_for_llm = [system_message] + self.messages`.

### Idle cleanup

`cleanup_idle_sessions()` runs every 30 minutes and evicts any interpreter that has been idle for > 1 hour (`IDLE_TIMEOUT = 3600 s`). Eviction calls `interpreter.reset()`, deletes the in-memory instance, and removes both Redis keys (`last_active:…` and `messages:…`).

### Conversation persistence (separate from Redis)

Redis is the **live working memory** (volatile, per-session). Long-term saves go through `routes/conversations.py` → PostgreSQL via `ConversationMessage` rows. Loading a saved conversation calls `POST /load-conversation`, which re-serialises stored DB messages into LMC format and writes them to the Redis `messages:` key, then destroys the in-memory interpreter so a fresh one picks up the loaded history on the next chat call.

---

## 16. Long Conversations — Full Context vs. Trimmed Slices

### The short answer

**The entire conversation is fed back to the LLM on every turn.** Open Interpreter does not summarise or chunk history by default. Before each LLM call, `respond()` builds:

```python
messages_for_llm = [system_message] + self.messages
```

`self.messages` is the **unbounded running log** of all user inputs, assistant text, code blocks, and computer (execution) outputs since the session started.

### What that means in practice for IDEA

| Setting | Value set in `interpreter_manager.py` |
|---|---|
| `context_window` | 400,000 tokens |
| `max_completion_tokens` | 64,000 tokens |
| `max_output` | 64,000 chars (per code-execution output, stored truncated) |

With a 400k-token context window the conversation can grow very long before any trimming kicks in.

### When trimming does kick in (`tokentrim`)

Inside `Llm.run()`, before building the API payload:

```
target budget = context_window - max_tokens - 25
             = 400,000 - 64,000 - 25 = ~335,975 tokens

tokentrim splits messages into:
  • text messages  (role=user/assistant, type=message)
  • non-text       (code blocks, console output, images)

text messages are dropped from the MIDDLE of the history first
non-text messages are reinserted at their original positions
```

So in a very long session:
- **Code blocks and execution outputs are preserved** (they carry execution state the LLM needs).
- **Conversational prose from the middle of the history is trimmed first** to free up budget.
- The most recent turns (both ends of the message list) are always kept.

### Per-output truncation (`max_output = 64,000`)

Before any trimming, individual execution outputs are already capped. In `_respond_and_store()`, if a `console` output exceeds `interpreter.max_output` characters it is truncated in-place inside `self.messages`. This prevents a single runaway `print` statement from consuming the entire context budget.

### Image handling in long conversations

For OS-mode (screenshots): only the **last 2 images** are kept in the message list before each LLM call. For non-OS mode: all images except the first and last 2 are dropped. This is an aggressive prune that runs independently of `tokentrim`.

### The full picture for a turn N in a long conversation

```
Turn N incoming:
  Redis  →  interpreter.messages  (full history, turns 1 … N-1)
  chat()    appends turn N user message

  respond() builds messages_for_llm:
    [system_message]                ← rebuilt fresh every turn
    + interpreter.messages          ← all prior turns + new user msg
    + [loop_message if loop=True]   ← optional

  Llm.run() trims to fit 335,975 token budget:
    - drop middle text messages if over budget
    - keep code/console/image non-text at positions
    - prune images to last 2 (OS mode) or first+last 2 (non-OS)

  LLM receives: effectively the entire conversation,
                minus any middle prose that was trimmed out

  LLM responds → code executed → output appended to messages

  Redis  ←  interpreter.messages  (full updated history saved)
```

### Summary table

| Aspect | Behaviour |
|---|---|
| History strategy | Full replay every turn (no summarisation) |
| Text trimming | `tokentrim` drops middle prose when over ~336k-token budget |
| Code/output trimming | Non-text preserved; individual outputs capped at 64k chars |
| Image trimming | Last 2 kept (OS mode); first + last 2 kept (non-OS) |
| State persistence | Redis per-session; PostgreSQL for saved conversations |
| Idle eviction | After 1 hour of inactivity (history deleted from Redis) |

---

## Summary: How the LLM "Thinks"

1. **It is told it's a world-class programmer** with full permission to run any code.
2. **It plans first** (the system prompt nudges it to write a plan for complex tasks).
3. **It executes incrementally** — small code blocks, prints intermediate results, reads output, adjusts.
4. **It sees its own output** — every code execution result is fed back as a `role: computer` message, so the LLM can reason about what happened and decide next steps.
5. **It loops** until it decides there's nothing more to do — either by running out of code to write, or in `loop=True` mode, by saying one of the magic termination phrases.
6. **It recovers from errors** — tracebacks are fed back as computer messages; the system prompt tells it "if at first you don't succeed, try again."
7. **It can use the OS** — in `os=True` mode it can screenshot, move the mouse, type, etc., making it a full computer-use agent.
