#!/usr/bin/env bash
# Preserve the existing SQLite-backed Open WebUI volume before changing images.
set -euo pipefail

container=idea_openwebui
target_image=idea-openwebui:0.12.5-idea.0.11

if ! docker inspect "$container" >/dev/null 2>&1; then
  volume="$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["volumes"]["idea_openwebui_data"]["name"])')"
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

was_running="$(docker inspect "$container" --format '{{.State.Running}}')"
if [[ "$was_running" == true ]]; then
  docker stop -t 30 "$container" >/dev/null
fi
backup_complete=false
restore_on_failure() {
  if [[ "$backup_complete" != true && "$was_running" == true ]]; then
    echo "Backup failed; restarting the previous Open WebUI container." >&2
    docker start "$container" >/dev/null || true
  fi
}
trap restore_on_failure EXIT

umask 077
mkdir -p .local-validation/openwebui-deploy-backups
backup="$(mktemp -d "${PWD}/.local-validation/openwebui-deploy-backups/backup-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
mkdir "$backup/data"
docker inspect "$container" --format '{{.Image}}' > "$backup/previous-image-id.txt"
printf '%s\n' "$volume" > "$backup/volume-name.txt"
docker cp "$container:/app/backend/data/." "$backup/data/"

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
