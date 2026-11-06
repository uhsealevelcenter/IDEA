#!/usr/bin/env bash
# Preserve the existing SQLite-backed Open WebUI volume before changing images.
set -euo pipefail

container=idea_openwebui
compose_config="$(docker compose config --format json)"
target_image="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["openwebui"]["image"])' <<< "$compose_config")"

if ! docker inspect "$container" >/dev/null 2>&1; then
  volume="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["volumes"]["idea_openwebui_data"]["name"])' <<< "$compose_config")"
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    echo "Open WebUI container is missing but its data volume exists; back it up before deploying." >&2
    exit 1
  fi
  echo "No existing Open WebUI container; skipping pre-upgrade backup."
  exit 0
fi

current_image="$(docker inspect "$container" --format '{{.Config.Image}}')"
if [[ "$current_image" == "$target_image" ]]; then
  echo "Open WebUI already uses the target image; skipping pre-upgrade backup."
  exit 0
fi

volume="$(docker inspect "$container" --format '{{range .Mounts}}{{if and (eq .Destination "/app/backend/data") (eq .Type "volume")}}{{.Name}}{{end}}{{end}}')"
if [[ -z "$volume" ]]; then
  echo "Open WebUI has no named data volume at /app/backend/data; review backup configuration." >&2
  exit 1
fi

# Measure and enumerate only the data that will be copied. Open WebUI is still
# running here so insufficient space does not cause downtime.
umask 077
backup_root="${PWD}/.local-validation/openwebui-deploy-backups"
mkdir -p "$backup_root"
manifest="$(docker exec "$container" python -c '
import json
from pathlib import Path
root = Path("/app/backend/data")
entries = sorted(p.name for p in root.iterdir() if p.name != "cache")
size = 0
for name in entries:
    path = root / name
    files = path.rglob("*") if path.is_dir() else [path]
    for item in files:
        if item.is_file():
            size += item.stat().st_size
print(json.dumps({"entries": entries, "bytes": size}))
')"
python3 - "$backup_root" "$manifest" <<'SPACE'
import json
from pathlib import Path
import shutil
import sys

root, manifest = sys.argv[1], json.loads(sys.argv[2])
entries = manifest["entries"]
if "webui.db" not in entries or any(
    not isinstance(name, str) or not name or name in (".", "..", "cache")
    or Path(name).name != name for name in entries
):
    raise SystemExit("Invalid Open WebUI backup manifest")
# Allow 20% growth/overhead and leave at least 1 GiB free after the copy.
required = (manifest["bytes"] * 12 + 9) // 10 + 1024**3
free = shutil.disk_usage(root).free
print(f"Open WebUI backup: {manifest['bytes']} bytes excluding cache; "
      f"requires {required} free bytes; available {free}")
if free < required:
    raise SystemExit("Insufficient disk space for Open WebUI backup; container was not stopped")
SPACE

was_running="$(docker inspect "$container" --format '{{.State.Running}}')"
backup_complete=false
backup=""
restore_on_failure() {
  if [[ "$backup_complete" != true ]]; then
    if [[ -n "$backup" ]]; then
      rm -rf -- "$backup"
    fi
    if [[ "$was_running" == true ]]; then
      echo "Backup failed; restarting the previous Open WebUI container." >&2
      docker start "$container" >/dev/null || true
    fi
  fi
}
trap restore_on_failure EXIT
if [[ "$was_running" == true ]]; then
  docker stop -t 30 "$container" >/dev/null
fi

backup="$(mktemp -d "$backup_root/backup-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
mkdir "$backup/data"
docker inspect "$container" --format '{{.Image}}' > "$backup/previous-image-id.txt"
printf '%s\n' "$volume" > "$backup/volume-name.txt"
# Stream the stopped container's current directory, excluding cache on extraction.
# Enumerating files after shutdown also handles SQLite removing its WAL/SHM files
# during a clean checkpoint. pipefail catches either copy or extraction failures.
docker cp "$container:/app/backend/data/." - | \
  tar --extract --file=- --directory="$backup/data" \
    --anchored --exclude='./cache' --exclude='cache' --no-same-owner

python3 - "$backup/data/webui.db" <<'PY'
from pathlib import Path
import sqlite3
import sys

path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit("Backup has no webui.db; review the Open WebUI database configuration")
with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
    result = db.execute("PRAGMA integrity_check").fetchall()
if result != [("ok",)]:
    raise SystemExit("Open WebUI backup database integrity check failed")
print("Open WebUI backup database integrity check: ok")
PY

touch "$backup/VERIFIED"
backup_complete=true
echo "Verified Open WebUI backup: $backup"

# Prune only verified backups, and only after the new copy passes validation.
# Keep only this verified backup. Unknown files stay intact.
python3 - "$backup_root" "$backup" <<'RETENTION'
from pathlib import Path
import shutil
import sys

root, current = map(Path, sys.argv[1:])
previous = sorted(
    (p for p in root.glob("backup-*") if p != current and not p.is_symlink()
     and p.is_dir() and (p / "VERIFIED").is_file()),
    key=lambda p: p.stat().st_mtime_ns, reverse=True,
)
for old in previous:
    shutil.rmtree(old)
    print(f"Removed older Open WebUI backup: {old.name}")
RETENTION
