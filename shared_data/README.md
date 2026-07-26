# IDEA shared scientific data

IDEA exposes one centrally managed dataset tree to every user's terminal at
`/app/data`. The terminal mount is read-only; only the maintenance container
can update it.

The allowlist in `manifest.toml` intentionally includes only:

- `metadata/fd_metadata.geojson`
- `benchmarks/all_benchmarks.json`
- `altimetry/cmems_altimetry_regrid.nc`
- `InSight/`

Pointing the importer at legacy IDEA's complete `data/` directory cannot copy
its papers, HCDP, SJW, `.pqa`, prompts, or any other unlisted path.

## Initial import from legacy IDEA

The maintenance container mounts `idea_shared_data` read/write. Supply the
legacy directory as an additional read-only mount:

```bash
docker compose run --rm --build \
  --volume "$(realpath ../../IDEA/data):/source:ro" \
  shared-data import /source

docker compose run --rm shared-data status
```

Then rebuild/restart the sandbox service and recreate existing microVMs once,
because Microsandbox attaches mounts only when a sandbox is created:

```bash
docker compose up -d --build sandbox
./interpreter_kernel/refresh_sandboxes.sh --skip-pull
```

`refresh_sandboxes.sh` permanently replaces each user's old microVM filesystem.
Files already synchronized to Open WebUI remain available there.

## Updating altimetry

The manifest contains the current UHSLC URL for the regridded altimetry file:

```bash
docker compose run --rm shared-data update altimetry
docker compose run --rm shared-data status --dataset altimetry
```

Downloads are validated with `ncdump`, installed with an atomic rename, and
recorded in `/app/data/.idea-shared-data-state.json`. Existing user microVMs
see the replacement through their shared mount; they do not need recreation
for ordinary data updates.

For metadata, benchmarks, or InSight updates, import a curated source tree:

```bash
docker compose run --rm \
  --volume "/absolute/path/to/curated-data:/source:ro" \
  shared-data import /source --dataset metadata --dataset benchmarks
```

Directory datasets use versioned releases and an atomically replaced symlink.
The two newest directory releases are retained for safe in-flight readers and
rollback.
