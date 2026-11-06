# Sandbox lifecycle recovery

IDEA pins Microsandbox 0.7.2 and applies `sandbox_service/native/pid-identity.patch`
to upstream commit `60d4dc8a436fb9365491567ec21d073e924e3c6d` when building the
sandbox image. The SDK's Linux start guard now checks the recorded process's
thread group, executable, sandbox name, and sandbox ID under its existing
native lifecycle locks. An unrelated reused PID no longer blocks resume.
Matching or ambiguous runtime identities remain protected. No catalog schema,
guest protocol, image format, or user disk is changed by the patch.

The native extension exports an IDEA capability marker. The service refuses to
attach with an unpatched SDK, rather than silently reverting to PID-only checks.
The existing reconnect handler refreshes state and connects only to Running VMs.
Failed initialization releases its background event-loop thread.

## SQLite safety

The old Python compatibility guard and deployment probe opened the live native
catalog through `sqlite3`. That design was unsafe: journal files held by the
native SDK could be unlinked, followed by delayed catalog corruption. Native
and Python database interoperability must not be assumed, even across separate
processes. All Python SQLite catalog access has been removed from live service
modules. Runtime/catalog changes use the native SDK exclusively.

Do not run Python SQLite integrity checks or metadata repairs against a live
Microsandbox catalog. First stop all SDK, CLI, and VM catalog writers and retain
the complete database/journal set. User `upper.ext4` disks are separate from the
catalog and must not be recreated as a metadata recovery shortcut.

The entrypoint runs `offline_catalog_check.py` before loading the native SDK or
starting Uvicorn. It refuses to open the catalog if another Python/API/msb
process is present in the container. Integrity or foreign-key failures prevent
startup. This checker is a separate, startup-only process and must never be
called from an API handler or a live health check.

## Verification

- The native patch includes Rust tests for recycled worker TIDs, unrelated
  processes, another sandbox's PID, matching ownership, and ambiguous metadata.
- Python regressions reject an unpatched native extension, verify state-aware
  reconnect and thread cleanup, and prohibit `sqlite3` imports in live modules.
- Deployment runs `/app/lifecycle_smoke.py` against the reserved
  `idea-lifecycle-smoke` sandbox (1 vCPU, 1024 MiB, 4096 MiB sparse upper disk).
  It checks the previous deployment's marker, performs three stop/resume cycles,
  verifies the marker and working directory each time, and rejects unlinked
  catalog/journal descriptors in its own process or the API process. It leaves
  the probe stopped. It never injects faults into a live database.
- Corruption recovery validation must include repeated native SDK operations,
  process/container restarts, then an **offline** integrity and foreign-key check.
  An immediate successful resume alone does not establish long-term integrity.

The extension is compiled reproducibly in a Rust 1.94 builder using the pinned
upstream lockfile. The installed `msb` CLI remains v0.7.2. On any SDK/runtime
upgrade, revalidate the patch and remove it when upstream provides equivalent
process-identity validation. Reinstalling the stock Python wheel requires
rebuilding the service image to restore the native patch.

This fix does not address the separate FileNav POST `/files/cwd` compatibility
or outer-container filesystem fallback behavior. A listing HTTP 200 alone is
insufficient evidence of successful guest attachment.
