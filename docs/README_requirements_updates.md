# Updating IDEA Dependencies

Follow these steps whenever Python package requirements change.

1. **Adjust dependencies**
   - Edit `pyproject.toml` to add/remove packages or update git/branch refs.
   - If a lock entry needs manual correction (e.g., a git revision), run `uv lock --refresh-package <name>` after editing.

2. **Regenerate the lockfile**
   - From the repo root, run `uv lock` (or limit it with `--refresh-package …`) so `uv.lock` matches the new dependency graph.
   - Commit this file whenever it changes.

3. **Regenerate `requirements.txt`**
   - Export from the lock:  
     `uv export --format=requirements-txt --no-hashes --no-emit-project --output-file requirements.txt`
   - This keeps Docker builds (and other pip-based workflows) aligned with the lock.

4. **Rebuild and verify**
   - Restart the local stack (`./local_start.sh`) so containers pick up the refreshed dependencies.
   - Run targeted tests or smoke checks to confirm the app still starts and key flows work.

5. **Commit + document**
   - Commit the updated `pyproject.toml`, `uv.lock`, `requirements.txt`, and any related code or documentation.
   - Mention notable dependency changes in the commit message or changelog if they affect other developers.

Following this checklist keeps all dependency metadata synchronized and prevents surprises during Docker builds or deployments.
