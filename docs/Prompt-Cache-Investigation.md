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

The system prompt uses a native `input_text` block for Responses requests.
LangChain passes that block through, preserving the breakpoint. Chat
Completions requests use `text`, which retains the breakpoint on that API.
Both manual and graph execution pass their configured API choice to the
same system-message helper. Unsupported models continue to receive plain
system-message text.

`tests/test_prompt_cache.py` constructs a real `TerminalGraphRuntime` and
`TerminalAgent` and captures serialized HTTP bodies for streamed Responses
and Chat Completions requests with tools bound, including manual execution.
It checks the breakpoint, explicit mode, and session cache key for
`gpt-5.6-terra`, `gpt-6-astra`, and `gpt-6.1-sol`, across changing user
requests. It fails on the missing breakpoint before the fix and passes
afterward. The Chat Completions regression test also reproduced the
incorrect `input_text` block before the endpoint-aware fix and passes
afterward. No provider requests are made by these tests.

Run the focused tests with:

```bash
LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python -m unittest discover -s tests -p test_prompt_cache.py
LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python -m unittest discover -s tests -p test_vision.py
```

Both focused suites pass (31 tests). The six other suites listed in
`langgraph/README.md` produced 71 passes and two errors in unchanged
`MicrosandboxPythonStreamingTests` cleanup: `Cannot close a running event
loop`. Those two errors also reproduce when the output-archiving suite is
run alone; that suite does not import the changed agent module.

## Local dependency validation

The local virtual environment was upgraded from LiteLLM `1.83.14` to the
repository pin, `1.101.0`. All 29 focused tests pass after the upgrade.
An offline check of the pinned version's OpenAI Responses parameter mapping
and request transformation preserved both cache options and breakpoint
with `drop_params=True`; this does not verify the deployed proxy path.

The upgrade exposes a dependency conflict: installed `fhlmi==1.0.5`
(required by `paper-qa==2026.1.5`) declares
`litellm>=1.83.9,<=1.84.1`. Consequently `pip check` fails with the repository's
LiteLLM pin. The cache tests do not establish PaperQA runtime compatibility;
the conflicting dependency constraints still need reconciliation.

## Live local verification — November 5, 2026

Three live requests through `TerminalGraphRuntime.model_messages()` and
`call_model()` confirmed provider prompt-cache reads on `gpt-6.1-sol` with
medium reasoning. The test used IDEA's actual system prompt, built-in skill
manifest, and 14 bound tools, through the local LiteLLM proxy. Tool choice
was disabled and output limited to 256 tokens for this diagnostic; no tools
were executed.

| Request | Input tokens | Cached input tokens | Duration |
| --- | ---: | ---: | ---: |
| 1 | 7,608 | 0 | 3.89 s |
| 2 | 7,608 | 7,484 | 1.17 s |
| 3 | 7,608 | 7,484 | 1.20 s |

Both repeat requests read approximately 98.4% of input tokens from the
provider cache. All three outgoing HTTP bodies preserved explicit mode and
one system-prompt breakpoint, with identical system/tool prefix hashes.
User messages differed for every request and provider response IDs were
distinct. These were separate model calls, not replayed proxy responses.
Provider cache-write token counts were not exposed in the returned usage.
IDEA's `_model_usage_record()` reported the cache reads shown above.

Run ID: `cache-verification-3a6808aad01b4dca8d35a7b0c1a5fa55`.

The first attempts exposed two local configuration issues: the existing
virtual key lacked `gpt-6.1-sol` access, and the running proxy had not loaded
the already-configured route. The model was added to the existing key's
allowlist while preserving its prior models and budget/rate limits.
Restarting the proxy encountered a stale Docker Desktop bind-mount source;
recreating only the LiteLLM container from its existing image restored the
mount and loaded the route. The proxy was healthy afterward.

This verifies the updated application model-call path through the local
proxy and upstream provider. It does not verify the Open WebUI/chat-run
endpoint flow or production traffic. After deploying the fix, repeat this
check against production and inspect `cached_input_tokens` telemetry.
