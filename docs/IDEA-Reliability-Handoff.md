# IDEA Reliability Handoff

## Objective

Fix three production reliability failures observed on 14–15 August 2026:

1. Python Stop is only cooperative and can leave a user sandbox locked for 30–60 minutes.
2. An OOM-killed Jupyter execution kernel is not detected or recreated, leaving its stream bridge waiting forever.
3. The two newest tool observations are exempt from context limits, allowing one large Python result to exceed the model context window.

The work should preserve persistent Python variables and user files when possible, while guaranteeing that a failed execution cannot make IDEA unusable indefinitely.

## Production incidents and evidence

### Incident 1: Stop could not terminate threaded Python

Run `c51bcf51e7ba41faa421fb348b92edb3` executed:

```python
with ThreadPoolExecutor(max_workers=12) as ex:
    loaded = list(ex.map(loadf, stations))
```

Timeline:

- Python started at `2026-08-14 22:08:44 UTC`.
- Stop was requested at `22:12:46`.
- The sandbox stream timed out at `22:39:01`.
- The run did not reach `stopped` until `23:09:01`.
- Restarting `idea_sandbox` restored service.

Jupyter interrupted the main execution thread, but `ThreadPoolExecutor.__exit__()` waited for worker threads. IDEA sent one `SIGINT`, did not confirm termination, and had no escalation path. The 1,800-second SDK/HTTP limits then produced successive 30-minute waits.

### Incident 2: OOM-killed kernel poisoned the sandbox

Run `ca6b05f303974a3e9eb3d84679940fc1` downloaded 495 station JSON products and then attempted to deserialize all of them into three lists inside a 1 GiB microVM.

Guest `dmesg` confirmed:

```text
python3 invoked oom-killer
Out of memory: Killed process 503 (python)
anon-rss: 703680kB
```

Afterward:

- PID 503 remained as a zombie execution kernel.
- The kernel daemon remained alive.
- The `client.py --run-stream-file` bridge remained blocked waiting for a response from the dead kernel.
- The microVM remained reachable and reported `running`, so ordinary health checks did not identify the broken kernel.
- The sandbox service stopped answering `/health` and subsequent runs blocked.
- Stop returned HTTP 200 even though there was no live execution left to interrupt.

### Incident 3: one tool observation exceeded model context

Run `ecbdaa30a6a84f0480deb892f7f5fd26` printed a complete nested `local_time` product:

```python
print("local", fl.get("local_time"))
```

The resulting console observation was 1,938,839 characters. The next model request contained 1,029,896 input tokens, exceeding LiteLLM's configured 922,000-token limit. The Terra-to-Luna fallback could not help because both received the same oversized prompt.

Open WebUI autocompaction was not defective in this case. It runs between user turns, before the IDEA Pipe starts. At the beginning of this turn, context was only about 16,000 tokens. The oversized result was generated later inside LangGraph, where Open WebUI cannot compact intermediate tool messages.

IDEA has `IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES=6000`, but `compact_turn_messages()` exempts the two newest tool observations through `keep_recent_tools=2`. The 1.94 MB result was the newest observation and was passed to the model intact.

## Relevant code

### Cancellation and execution lifecycle

- `sandbox_service/msb_sandbox.py`
  - `_run()` / `_exec()` synchronous SDK bridge
  - `run_python_stream()`
  - cancellation monitor sends one `SIGINT`
  - queue loop has no independent wall-clock deadline
  - `future.cancel()` does not kill a guest process
  - `interrupt_python()` opens a second guest shell request
- `sandbox_service/terminal_registry.py`
  - per-sandbox lock is retained for the full Python stream
  - `_active_python_runs` and `_cancelled_python_runs`
  - streamed `interrupt_run()` returns `True` after only recording a flag
- `sandbox_service/main.py`
  - `/run-python/stream`
  - `/runs/{run_id}/interrupt`
- `interpreter_kernel/client.py`
  - guest bridge uses `HTTPConnection(..., timeout=None)`
  - `SIGINT` is forwarded to the daemon rather than terminating the bridge
- `interpreter_kernel/daemon.py`
  - daemon retains per-kernel runner and lock
  - it does not reliably detect that its ipykernel child was OOM-killed
- `langgraph/langgraph_service.py`
  - `/chat-runs/{run_id}/stop` publishes `stopping` and calls sandbox interrupt
  - it treats a cooperative interrupt request as sufficient
- `langgraph/idea_config.py`
  - `SANDBOX_HTTP_READ_TIMEOUT_SECONDS=1800`
- `docker-compose.yml`
  - microVM memory is 1 GiB by default
  - idle timeout is 1,800 seconds, but active/broken executions are not idle

### Model-context handling

- `langgraph/idea_graph/memory.py`
  - `compact_turn_messages(..., keep_recent_tools=2)`
  - only older observations are bounded to 6,000 bytes
- `langgraph/idea_graph/runtime.py`
  - `model_messages()` assembles conversation and live turn messages
  - `run_python_tool` streams full display output and constructs the tool outcome
- `langgraph/idea_graph/graph.py`
  - calls the model without a hard final prompt-size preflight
- `langgraph/idea_config.py`
  - `IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES=6000`
  - `IDEA_MAX_TOOL_RESULT_EXCERPT_BYTES=12000` is a separate execution-ledger limit
- `openwebui/functions/idea_pipe.py`
  - forwards the Open WebUI-selected/compacted branch
  - sanitizes persisted assistant tool display history
- Open WebUI request middleware applies conversation compaction before invoking the Pipe; it cannot compact LangGraph's in-progress tool observations.

## Required behavior

### 1. Escalating cancellation

Implement a run lifecycle with observable states such as:

```text
running -> interrupt_requested -> interrupt_sent -> terminated
                                      | grace expired
                                      v
                              kernel_restarted
                                      | failed
                                      v
                              sandbox_restarted
```

Recommended policy:

1. Send Jupyter `SIGINT`.
2. Wait 5–10 seconds for confirmed run termination.
3. If still running, terminate the guest bridge and replace the affected kernel.
4. If kernel replacement fails, stop/resume that user's microVM and evict the cached terminal handle.
5. Report whether execution was actually terminated or escalated. Do not return `interrupted=True` merely because a cancellation flag was stored.

Cancellation and recovery control must not acquire the execution lock held by the stuck run.

### 2. Kernel death and OOM recovery

- Monitor the ipykernel child process or its ZMQ connection.
- If the kernel exits, is OOM-killed, or stops responding, close the active daemon response and stream bridge immediately.
- Return a classified error such as `kernel_oom` or `kernel_died` instead of waiting 30 minutes.
- Recreate that kernel automatically for the next cell.
- Reap dead children; do not leave zombies.
- Preserve the microVM filesystem. Losing in-memory Python variables after an OOM is acceptable and should be communicated clearly.
- Add a hard execution deadline enforced outside user Python. Do not rely on socket inactivity, SDK shell startup timeouts, or microVM idle timeout.

### 3. Bound every model-facing tool observation

- Apply a hard byte/token bound to every `ToolMessage`, including the newest observations.
- Keep full output available for UI streaming and in the execution archive.
- Model context should receive a bounded head/tail excerpt, a concise truncation notice, and the archive/execution reference.
- Add a final prompt-size preflight before every model call. If the assembled request exceeds a safe budget, compact/truncate locally before calling LiteLLM.
- A model fallback is not a solution when all fallback models receive the same oversized input.

Consider two limits rather than an exemption:

- Older observations: 6,000 bytes.
- Recent observations: a larger but finite limit, for example 24–64 KiB.

The exact values are configurable, but no single tool result may remain unlimited.

## Tests to add

### Cancellation integration tests

- `while True: pass`
- long `time.sleep()` loop
- code that catches or delays `KeyboardInterrupt`
- `ThreadPoolExecutor` with blocked workers
- blocked network read
- spawned child process
- cancellation during VM creation/kernel warmup
- cancellation after the guest bridge starts but before daemon registration

For every case, assert:

- Stop reaches a terminal state within a bounded time.
- a new run for the same user can start promptly.
- unrelated users and `/health` remain responsive.
- escalation state is accurately reported.

### Kernel failure tests

- Kill the ipykernel child during a stream.
- Simulate guest OOM or abrupt kernel exit.
- Verify stream closure and classified error.
- Verify zombie reaping.
- Verify automatic kernel recreation on the next execution.

### Context-limit tests

- Make a tool return a 2 MB string as the newest observation.
- Assert the next model-facing `ToolMessage` is bounded.
- Assert full output remains archived/displayable.
- Assemble a prompt near the configured model limit and verify preflight compaction.
- Retain tests for older-observation compaction, but remove the assumption that recent observations are unlimited.

## Suggested implementation order

1. Fix unbounded model-facing tool observations and add prompt preflight. This is localized and prevents immediate context-window failures.
2. Add kernel-process death detection and bridge cleanup.
3. Implement confirmed, escalating Stop with a short grace period.
4. Add per-run hard deadlines and independent watchdogs.
5. Add service/microVM health telemetry and operational recovery hooks.

## Operational notes

- `docker restart idea_sandbox` recovered both sandbox incidents but should remain an emergency fallback, not normal cancellation behavior.
- Restarting only `idea_sandbox` preserves the database and other IDEA services, but an unclean microVM stop can mark a sandbox `crashed`; recovery should use the microsandbox lifecycle APIs cleanly where possible.
- Docker `restart: unless-stopped` does not restart an unhealthy-but-running container. A health check alone is insufficient without a watchdog or orchestrator action.
- Do not solve the issue solely by increasing the 1 GiB microVM memory or the 30-minute timeout. Those changes only postpone failure.

## Acceptance criteria

- User Stop ends or escalates any Python run within 15 seconds under normal recovery and within a documented upper bound for microVM recovery.
- An OOM-killed kernel produces a classified error and the next Python cell works without restarting `idea_sandbox`.
- No model request can exceed its configured context window because of one tool observation.
- Open WebUI conversation compaction and LangGraph live-turn compaction have explicit, separate tests and documented responsibilities.
- Sandbox `/health` remains responsive during long or broken user executions.
- Existing persistence, streaming output, execution ledger, checkpointing, and per-user isolation tests continue to pass.

