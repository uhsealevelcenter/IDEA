# Prompt-cache serialization investigation

## Finding

IDEA requests explicit prompt caching but its system-message breakpoint is
lost before the request reaches LiteLLM. This was reproduced locally with
the repository-pinned `langchain-openai==1.1.10` and
`langchain-core==1.4.9`, using the real OpenAI SDK and an HTTP mock transport.

`_cacheable_system_message()` in `langgraph/agents/terminal_agent.py` built
a generic `text` content block containing `prompt_cache_breakpoint`.
LangChain's `_convert_chat_completions_blocks_to_responses()` rebuilds
these blocks as `{"type": "input_text", "text": ...}`, dropping every
other field. The top-level `prompt_cache_options: {"mode": "explicit"}`
survives, leaving the request without the explicit breakpoint it needs.

The earlier traffic counts supplied with this investigation were not
independently re-queried. The local reproduction confirms the serialization
defect underlying those observations; it does not establish a live hit rate.

## Fix and regression coverage

The system prompt now uses a native `input_text` block. LangChain passes
that block through, preserving the breakpoint. Both manual and graph
execution use the same system-message helper. Unsupported models continue
to receive plain system-message text.

`tests/test_prompt_cache.py` constructs a real `TerminalAgent` and captures
the serialized HTTP body for streamed Responses requests with tools bound.
It checks the breakpoint, explicit mode, and session cache key for
`gpt-5.6-terra`, `gpt-6-astra`, and `gpt-6.1-sol`, across changing user
requests. It fails on the missing breakpoint before the fix and passes
afterward. No provider requests are made by this test.

Run the focused tests with:

```bash
LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python -m unittest discover -s tests -p test_prompt_cache.py
LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python -m unittest discover -s tests -p test_vision.py
```

Both focused suites pass (29 tests). The six other suites listed in
`langgraph/README.md` produced 71 passes and two errors in unchanged
`MicrosandboxPythonStreamingTests` cleanup: `Cannot close a running event
loop`. Those two errors also reproduce when the output-archiving suite is
run alone; that suite does not import the changed agent module.

## Deployment verification still required

The locally installed LiteLLM is `1.83.14`, while the repository pins
`1.101.0`. An offline check of the local OpenAI Responses parameter mapping
and request transformation preserved both cache options and breakpoint
with `drop_params=True`; this does not verify the deployed proxy path.

After deploying the fix, capture an application request at the upstream
provider boundary and verify that both `prompt_cache_options` and the
system block's `prompt_cache_breakpoint` survive the deployed proxy.
Repeat application calls in one conversation with identical instructions
and tools, then inspect provider cache-read/write usage and IDEA's
`cached_input_tokens` telemetry. A preserved breakpoint alone does not
prove a provider cache hit.

This change was validated locally; no deployment or live traffic
verification was performed.
