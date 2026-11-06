# Sandbox lifecycle recovery

IDEA's pinned Microsandbox 0.7.2 start path checks whether the previous run's
PID exists, even when that run is Terminated. After a container replacement,
Linux may reuse that number for an API worker thread or another user's VM.
Starting the stopped sandbox then fails with SandboxStillRunningError.

## Compatibility guard

Before resuming an existing sandbox, IDEA's `lifecycle_recovery.py` uses the
same per-name transition and lifecycle flock files as Microsandbox. Under
those locks and a SQLite write transaction, it examines only the latest run.
It clears that run's PID only if the sandbox is Stopped/Crashed, the run is
Terminated, and `/proc` proves the PID is absent or belongs to an unrelated
thread/process. Live matching runtimes and ambiguous/unreadable identities
are preserved. Lock contention leaves recovery to the SDK. No processes are
signalled and no guest disk, file, image, or sandbox definition is replaced.
Every successful repair logs the sandbox, run ID, and old PID.

The SDK then performs its normal locked start. If another start wins, IDEA
refreshes state and connects only when it is Running. A second stale-PID
repair permits one retry; other lifecycle failures propagate without creating
a replacement sandbox. Failed terminal initialization also releases its event
loop thread.

The adapter is Linux-only and explicitly limited to SDK version 0.7.2 because
it uses private schema and lock conventions. The upstream source used to
validate those conventions is tag v0.7.2, commit
`60d4dc8a436fb9365491567ec21d073e924e3c6d`:

- `sdk/rust/lib/backend/local/sandbox/mod.rs`: start ordering and previous PID guard
- `crates/runtime/lib/client/ipc.rs`: lifecycle and transition locks
- `sdk/rust/lib/config/mod.rs`: home/run paths

On a Microsandbox upgrade, review this adapter and the integration probe
against upstream. Prefer removing the adapter when upstream fixes runtime
identity validation. Rolling back application code preserves disks but
reintroduces the original resume failure; already cleared PIDs remain valid
terminal history.

## Verification and deployment

Run focused regressions with:

```sh
python3 -m unittest sandbox_service/test_lifecycle_recovery.py sandbox_service/test_resource_limits.py
```

The deploy workflow runs these before replacing containers. Deployment then
runs `/app/lifecycle_smoke.py` in the sandbox service. This uses only the
reserved `idea-lifecycle-smoke` sandbox (1 vCPU, 1024 MiB, 4096 MiB sparse
upper disk). It checks the previous deployment's marker if the probe already
exists, writes a new marker, stops the VM, deliberately sets its terminated
run's PID to the live probe process, resumes it, and verifies the file and
working directory. It leaves the probe stopped and retains its disk for the
next deployment. Production deployment fails if the probe fails. Local-shell
deployments skip the microVM check.

The same real-VM probe was run before pushing: deployed code reproduced the
original SandboxStillRunningError/SandboxNotRunningError failure; the proposed
guard recovered the deliberate collision and preserved the marker.

This change addresses runtime resume and deployment detection. The separate
FileNav POST `/files/cwd` compatibility and outer-container listing fallback
issues are not changed here.
