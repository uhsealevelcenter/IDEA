# Microsandbox Resource Limits Handoff

## Objective and implementation status

Environment-configurable resource limits for each IDEA microsandbox are
implemented on the `next-dev` branch as described below.

- Preserve today's local-development defaults: 1 vCPU, 1024 MiB RAM, and the
  microsandbox default 4096 MiB writable OCI upper disk.
- Configure remote `staging` and `prod` deployments for 2 vCPUs, 4096 MiB RAM,
  and 10240 MiB writable disk per sandbox.
- Treat these values as per-sandbox ceilings/allocations. Do not change the
  resource limits of the surrounding `idea_sandbox` Docker service as a
  substitute.
- Do not add GPU access to user microVMs as part of this change. The pinned
  microsandbox runtime has no supported NVIDIA/CUDA passthrough mechanism.

The remote hosts currently provide 8 CPUs, 30 GB RAM, and 250 GB disk in
total. `prod` additionally has one quarter of a GPU with 10 GB VRAM.

## Previous behavior

Before this change, `sandbox_service/msb_sandbox.py` read:

```text
SANDBOX_CPUS       default 1
SANDBOX_MEMORY_MB  default 1024
```

Those values were passed as `cpus` and `memory` to `Sandbox.create()`. However,
`docker-compose.yml` did not forward either variable into the `sandbox`
container, so putting them only in `.env` had no effect.

There was no IDEA disk-size setting. With the pinned
`microsandbox==0.6.6`, an OCI sandbox uses a managed sparse ext4 writable
upper disk whose default virtual size is 4096 MiB. Version 0.6.6 exposes a
larger upper through:

```python
from microsandbox import Image

Image.oci(image_reference, upper_size_mib=desired_size_mib)
```

The installed CLI exposes the equivalent `--oci-upper-size` creation option.
Do not design this patch against newer `root_disk` APIs without deliberately
upgrading and fully revalidating microsandbox; v0.6.7 changed that API.

Resource settings apply when a microVM is created. The present reconnect path
starts an existing sandbox without reconciling its creation-time resources,
image, or mounts. Merely restarting the `sandbox` service therefore does not
resize existing user sandboxes.

## Implementation

### 1. Expose all three settings through Compose

The base `sandbox.environment` list in
`docker-compose.yml`:

```yaml
- SANDBOX_CPUS=${SANDBOX_CPUS:-1}
- SANDBOX_MEMORY_MB=${SANDBOX_MEMORY_MB:-1024}
- SANDBOX_DISK_MB=${SANDBOX_DISK_MB:-4096}
```

keeps ordinary local deployments on the existing limits without requiring
`.env` changes. The production Compose overlay replaces those defaults with
2 vCPUs, 4096 MiB RAM, and 10240 MiB disk. Explicit `.env` values override
either profile.

The same variables and units are documented in `example.env`. Blank values
select the active Compose profile defaults:

```dotenv
SANDBOX_CPUS=
SANDBOX_MEMORY_MB=
SANDBOX_DISK_MB=
```

Remote `.env` files are deployment state and are not committed. The
production overlay already selects these values, but operators may record
them explicitly in both staging and production:

```dotenv
SANDBOX_CPUS=2
SANDBOX_MEMORY_MB=4096
SANDBOX_DISK_MB=10240
```

### 2. Add writable-disk sizing to `MicrosandboxTerminal`

In `sandbox_service/msb_sandbox.py`, the implementation:

1. Adds `DEFAULT_DISK_MB`, read from `SANDBOX_DISK_MB` with a 4096 MiB default.
2. Adds a `disk_mb` constructor argument and stores it on the terminal.
3. Imports `Image` alongside `Sandbox` in the lazy microsandbox import.
4. For creation only, replaces the plain image string with:

   ```python
   Image.oci(self.image, upper_size_mib=self.disk_mb)
   ```

5. Continues passing `cpus=self.cpus` and `memory=self.memory`.

Only use `Image.oci` for the configured OCI guest-image path. If support for
bind-root or disk-image roots is added later, it will need a separate typed
configuration rather than forcing `upper_size_mib` onto every image source.

Validate configuration during service startup. Reject non-integer or
non-positive CPU, memory, and disk values with an actionable error. Consider
explicit conservative upper bounds, but do not invent bounds that conflict
with a deployment requirement.

### 3. Update validation and tests

Add unit coverage that verifies:

- absent variables retain 1 CPU, 1024 MiB RAM, and 4096 MiB disk;
- Compose forwards all three variables;
- configured values reach the `Sandbox.create()` call;
- its `image` argument is an OCI `ImageSource` with the requested upper size;
- zero, negative, and malformed values fail clearly;
- reconnecting to an existing sandbox does not claim that its resources were
  updated.

`sandbox_service/test_local_image.py` now accepts a disk size and verifies
inside the guest that `/` has approximately the requested capacity, allowing
for filesystem metadata/reserved-block differences.

The local unit tests do not import the KVM-only image smoke test:

```bash
python -m unittest \
  sandbox_service/test_resource_limits.py \
  sandbox_service/test_service_concurrency.py \
  sandbox_service/test_terminal_registry_cancellation.py
docker compose -f docker-compose.yml -f docker-compose.prod.yml config
```

Run `sandbox_service/test_local_image.py` separately in the built sandbox
service environment on a KVM-capable remote host; it requires the
`microsandbox` package and a working runtime.

## Capacity and admission-control caveat

The proposed per-sandbox limits do not cap the number of simultaneously
running sandboxes. On an 8-CPU/30-GB host:

- CPU reaches the nominal host total at four fully busy 2-vCPU sandboxes.
- RAM theoretically fits seven 4-GB sandboxes, but the host, Docker services,
  image cache, kernel, and filesystem cache need headroom. Do not plan for
  seven simultaneously memory-saturated guests.
- Ten-GB upper disks are sparse, so 25 sandboxes do not immediately consume
  250 GB, but they can collectively grow toward that amount. Host OS, Docker
  layers, the shared-data volume, databases, logs, and microsandbox image
  cache also use the same physical disk.

This patch should document the operational concurrency limit and monitoring
expectation. A separate admission-control change may be needed before usage
can grow beyond a small developer population. At minimum, alert on host RAM,
disk free space, and active sandbox count instead of assuming per-sandbox
limits prevent host exhaustion.

## Rollout and existing sandboxes

1. Merge and deploy the code/Compose changes.
2. Set the three higher values only in each remote deployment's `.env`.
3. Recreate the `sandbox` Compose service and verify its environment.
4. Test one disposable microVM and check `nproc`, memory/cgroup limits, and
   `df` for the root filesystem.
5. Decide explicitly how to migrate existing sandboxes.

The existing `interpreter_kernel/refresh_sandboxes.sh` recreates every
sandbox and permanently deletes its writable filesystem. It may be used only
for the current developer-only population after explicit agreement. It is not
an acceptable production-user migration. CPU/RAM live modification is not a
complete migration solution here: disk growth is a creation-time setting in
the pinned runtime, and the current IDEA reconnect path does not reconcile
resource configuration.

Before real-user rollout, implement or rehearse snapshot/restore migration,
or defer the new limits until sandboxes are naturally replaced. Report which
sandboxes still use the old limits during a mixed-version transition.

## Production GPU assessment

Do not attempt to expose the production GPU partition through
`MicrosandboxTerminal` in this implementation.

As of this handoff, microsandbox's GPU-support request remains open and the
available proposal is for a virtio-gpu/Venus Vulkan device, not NVIDIA CUDA
compute or transparent access to a 10-GB NVIDIA partition. The pinned 0.6.6
Python SDK has no GPU/device-passthrough creation parameter. Mounting
`/dev/nvidia*` into the outer Docker `sandbox` service would not make those
devices appear inside its nested microVMs.

Practical options are:

1. Leave the GPU unused by IDEA until microsandbox and its libkrun backend
   support the required compute-device passthrough.
2. Add a separate, tightly scoped GPU worker service on `prod`, launched with
   the NVIDIA container runtime. IDEA sandboxes would submit bounded jobs and
   exchange explicit input/output artifacts through an authenticated service
   API. This requires its own authorization, queueing, resource/time limits,
   file validation, observability, and threat model; it must not accept
   unrestricted host shell commands from an untrusted sandbox.
3. Run selected user workloads directly in per-job GPU-enabled containers.
   This gives weaker isolation than the current microVM model and should be a
   separate architecture/security decision, not an incidental Compose flag.

Because prod has only one quarter-GPU partition, assume it is one exclusive
10-GB compute device unless the provider explicitly supports further safe
partitioning. A GPU worker should serialize jobs initially; do not promise
2-CPU/4-GB/10-GB-per-sandbox GPU access to every concurrent microVM.

## Acceptance criteria

- A default local deployment still creates a 1-vCPU, 1024-MiB sandbox with a
  roughly 4-GiB writable root.
- New staging/prod sandboxes receive 2 vCPUs, 4096 MiB RAM, and a roughly
  10-GiB writable root.
- The running sandbox container exposes the selected configuration without
  exposing secrets.
- Disposable real-microVM tests validate the effective guest limits.
- Existing-sandbox behavior and migration risks are documented and never
  silently destructive.
- No GPU device is exposed to user microVMs, and any future GPU worker is
  tracked as a separate security-reviewed feature.

## Upstream references

- [Microsandbox v0.6.6 release](https://github.com/superradcompany/microsandbox/releases/tag/v0.6.6)
- [Microsandbox sandbox CLI resource options](https://github.com/superradcompany/microsandbox/blob/main/docs/cli/sandbox-commands.mdx)
- [Open GPU-support request](https://github.com/superradcompany/microsandbox/issues/291)
- [Open virtio-gpu/Venus proposal](https://github.com/superradcompany/microsandbox/pull/1194)
