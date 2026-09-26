#!/usr/bin/env bash
# ==============================================================================
# deployment/update_openwebui_key.sh
#
# Generates and activates an API key for the Open WebUI administrator user,
# enabling auth.enable_api_keys in webui.db, and updating OPENWEBUI_API_KEY
# across openwebui/.env, langgraph/.env, and the root .env.
#
# Can be run anytime the openwebui container is running.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

echo "==> Checking if Open WebUI container is running..."
if ! docker compose ps openwebui | grep -q "Up"; then
  echo "Error: Open WebUI container is not running. Start it with './deployment/deploy.sh dev' first." >&2
  exit 1
fi

echo "==> Ensuring API keys are enabled and syncing admin credentials..."
api_key="$(docker compose exec -T openwebui python -c '
import sqlite3, time, uuid, asyncio, os
from open_webui.utils.auth import get_password_hash

conn = sqlite3.connect("/app/backend/data/webui.db")

# 1. Enable API keys in config
conn.execute("UPDATE config SET value=\"true\" WHERE key=\"auth.enable_api_keys\"")

# 2. Sync admin email and password from environment if provided
admin_email = os.getenv("WEBUI_ADMIN_EMAIL", "").strip()
admin_pass = os.getenv("WEBUI_ADMIN_PASSWORD", "").strip()

u = conn.execute("SELECT id FROM user WHERE role=\"admin\" LIMIT 1").fetchone()
if not u:
    print("NO_ADMIN")
    exit(1)

uid = u[0]

if admin_pass:
    async def update_pwd():
        hashed = await get_password_hash(admin_pass)
        if admin_email:
            conn.execute("UPDATE user SET email=? WHERE id=?", (admin_email, uid))
            conn.execute("UPDATE auth SET email=?, password=? WHERE id=?", (admin_email, hashed, uid))
        else:
            conn.execute("UPDATE auth SET password=? WHERE id=?", (hashed, uid))
        conn.commit()
    asyncio.run(update_pwd())

new_key = "sk-" + uuid.uuid4().hex
now = int(time.time())

conn.execute("DELETE FROM api_key WHERE user_id=?", (uid,))
conn.execute("INSERT INTO api_key (id, user_id, key, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
             (f"key_{uid}", uid, new_key, now, now))
conn.commit()
print(new_key)
' 2>/dev/null || true)"

if [[ -z "${api_key}" || "${api_key}" == *"NO_ADMIN"* ]]; then
  echo "Error: Could not generate API key. Make sure an admin account exists in Open WebUI." >&2
  exit 1
fi

echo "==> Successfully retrieved admin API key: ${api_key:0:8}..."

update_env_file() {
  local file_path="$1"
  if [[ -f "${file_path}" ]]; then
    python3 -c "
from pathlib import Path
p = Path('${file_path}')
text = p.read_text()
lines = [f'OPENWEBUI_API_KEY=${api_key}' if l.startswith('OPENWEBUI_API_KEY=') else l for l in text.splitlines()]
p.write_text('\n'.join(lines) + '\n')
"
    echo "  -> Updated ${file_path}"
  fi
}

update_env_file "openwebui/.env"
update_env_file "langgraph/.env"
if [[ -f ".env" ]]; then
  update_env_file ".env"
fi

echo "==> Done! OPENWEBUI_API_KEY has been updated."
