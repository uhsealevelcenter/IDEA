import asyncio
import hashlib
import os
import json
import logging
import pickle
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from paperqa import Settings
from paperqa.settings import AgentSettings, IndexSettings
from paperqa.agents.search import get_directory_index, SearchIndex

logger = logging.getLogger(__name__)

# Per-user lock to prevent concurrent index builds from corrupting files.zip
_user_index_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()  # protects access to _user_index_locks


def _get_user_lock(user_id: Any) -> threading.Lock:
    """Return (or create) a per-user threading lock for index builds."""
    key = str(user_id)
    with _locks_lock:
        if key not in _user_index_locks:
            _user_index_locks[key] = threading.Lock()
        return _user_index_locks[key]

# Roots derived from environment
PQA_HOME = Path(os.getenv("PQA_HOME", "/app/data"))
PQA_ROOT = PQA_HOME / ".pqa"
SETTINGS_DIR = PQA_ROOT / "settings"
INDEXES_ROOT = PQA_ROOT / "indexes"
PAPERS_ROOT = Path(os.getenv("PAPER_DIRECTORY", str(PQA_HOME / "papers")))


def get_user_papers_dir(user_id: Any) -> Path:
    return PAPERS_ROOT / str(user_id)


def get_user_index_dir(user_id: Any) -> Path:
    return INDEXES_ROOT / str(user_id)


def get_user_settings_path(user_id: Any) -> Path:
    return SETTINGS_DIR / f"user_{user_id}.json"


def get_user_manifest_path(user_id: Any) -> Path:
    """Return the path for a per-user PaperQA manifest file.

    The manifest tracks which documents have been added and their metadata,
    allowing PaperQA to skip re-parsing/re-embedding on subsequent Docs rebuilds.
    """
    return get_user_index_dir(user_id) / "manifest.csv"


def get_user_settings_name(user_id: Any) -> str:
    # PaperQA CLI expects a name without extension; it will append .json
    return f"user_{user_id}"


# ---------------------------------------------------------------------------
# Index status helpers (observability for background index builds)
# ---------------------------------------------------------------------------

def _index_status_path(user_id: Any) -> Path:
    return get_user_index_dir(user_id) / "index_status.json"


def write_index_status(
    user_id: Any,
    status: str = "ready",
    error: Optional[str] = None,
) -> None:
    """Write a small JSON file recording the current index-build status."""
    path = _index_status_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        payload["last_error"] = error
    path.write_text(json.dumps(payload, indent=2))


def read_index_status(user_id: Any) -> dict:
    """Read the index-build status for a user (returns empty dict if missing)."""
    path = _index_status_path(user_id)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


# ---------------------------------------------------------------------------
# Docs disk-cache helpers (pickle-based, shared between FastAPI and OI)
# ---------------------------------------------------------------------------

def _docs_cache_dir(user_id: Any) -> Path:
    return get_user_index_dir(user_id) / "docs_cache"


def _docs_pkl_path(user_id: Any) -> Path:
    return _docs_cache_dir(user_id) / "docs.pkl"


def _docs_revision_path(user_id: Any) -> Path:
    return _docs_cache_dir(user_id) / "revision.txt"


def _compute_revision(index_files: dict) -> str:
    """Compute a stable revision fingerprint from the set of indexed file names."""
    return hashlib.md5(str(sorted(index_files.keys())).encode()).hexdigest()


def save_docs_to_disk(user_id: Any, docs: Any, revision: str) -> None:
    """Pickle a Docs object + revision to disk for cross-process reuse.

    If pickling fails (complex objects, etc.) the partial files are cleaned up
    and a warning is logged — the system falls back to building Docs on query.
    """
    cache_dir = _docs_cache_dir(user_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    pkl_path = _docs_pkl_path(user_id)
    rev_path = _docs_revision_path(user_id)
    try:
        with open(pkl_path, "wb") as f:
            pickle.dump(docs, f, protocol=pickle.HIGHEST_PROTOCOL)
        rev_path.write_text(revision)
        logger.info(f"[PQA] Docs cache saved to disk for user {user_id}.")
    except Exception as exc:
        logger.warning(f"[PQA] Failed to pickle Docs for user {user_id}: {exc}")
        pkl_path.unlink(missing_ok=True)
        rev_path.unlink(missing_ok=True)


def load_docs_from_disk(user_id: Any, expected_revision: str) -> Any:
    """Load a pickled Docs object from disk if the revision matches.

    Returns the Docs object on success, or None on miss / error.
    """
    pkl_path = _docs_pkl_path(user_id)
    rev_path = _docs_revision_path(user_id)
    if not pkl_path.exists() or not rev_path.exists():
        return None
    if rev_path.read_text().strip() != expected_revision:
        return None
    try:
        with open(pkl_path, "rb") as f:
            return pickle.load(f)  # noqa: S301 — trusted internal cache
    except Exception as exc:
        logger.warning(f"[PQA] Failed to load Docs from disk for user {user_id}: {exc}")
        pkl_path.unlink(missing_ok=True)
        rev_path.unlink(missing_ok=True)
        return None


def clear_docs_cache(user_id: Any) -> None:
    """Remove the on-disk Docs cache for a user."""
    cache_dir = _docs_cache_dir(user_id)
    if cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)


def ensure_user_dirs(user_id: Any) -> None:
    get_user_papers_dir(user_id).mkdir(parents=True, exist_ok=True)
    get_user_index_dir(user_id).mkdir(parents=True, exist_ok=True)
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)


def ensure_user_pqa_settings(user_id: Any) -> Path:
    """Ensure a per-user PaperQA settings file exists and points to that user's papers and index directories.

    Returns the absolute path to the user-specific settings JSON.
    """
    ensure_user_dirs(user_id)

    user_settings_path = get_user_settings_path(user_id)

    # LEGACY: pqa_settings.json is only used as a base template for the per-user
    # JSON debugging file.  The *actual* runtime Settings object is built entirely
    # by get_user_settings() -> create_pqa_settings() from my_pqa_settings.py.
    base_settings_path = SETTINGS_DIR / "pqa_settings.json"

    settings_data: dict = {}
    if base_settings_path.exists():
        try:
            settings_data = json.loads(base_settings_path.read_text())
        except Exception:
            settings_data = {}

    # Ensure nested structures exist
    agent = settings_data.get("agent") or {}
    index = agent.get("index") or {}

    # Override with per-user paths
    index["paper_directory"] = str(get_user_papers_dir(user_id))
    index["index_directory"] = str(get_user_index_dir(user_id))
    # Store file paths relative to paper_directory to match sync comparison logic
    index["use_absolute_paper_directory"] = False
    index["sync_with_paper_directory"] = True
    index["recurse_subdirectories"] = False

    agent["index"] = index
    settings_data["agent"] = agent

    # Write the user-specific settings
    user_settings_path.write_text(json.dumps(settings_data, indent=2))
    return user_settings_path


def get_user_settings(user_id: Any) -> Settings:
    """Build a PaperQA Settings object for the user.
    
    This uses the comprehensive Python-based settings from my_pqa_settings.py
    with user-specific paper and index directories.
    
    Multi-tenant support:
    - Each user gets their own paper_directory: /app/data/papers/<user_id>/
    - Each user gets their own index_directory: /app/data/.pqa/indexes/<user_id>/
    - A per-user JSON file is also created at /app/data/.pqa/settings/user_<user_id>.json
      for debugging/inspection (though the actual Settings object uses Python config)
    """
    # Ensure directories and per-user JSON file exist (for debugging/inspection)
    ensure_user_pqa_settings(user_id)
    
    user_papers_dir = get_user_papers_dir(user_id)
    user_index_dir = get_user_index_dir(user_id)
    user_manifest = get_user_manifest_path(user_id)
    
    # Use the Python-based settings module for comprehensive configuration
    from utils.my_pqa_settings import create_pqa_settings
    
    settings = create_pqa_settings(
        paper_directory=user_papers_dir,
        index_directory=user_index_dir,
        manifest_file=user_manifest,
    )
    
    return settings


async def build_user_index(user_id: Any) -> SearchIndex:
    """Build/reuse the user's PaperQA index using Python API.
    
    This replaces the subprocess-based CLI approach with direct Python calls.
    The index is persisted to disk and reused on subsequent calls.
    Writes index_status.json so callers can observe progress.
    
    After building the search index, it also pre-builds a Docs object
    (with all papers added) and pickles it to disk so the first query
    can load it instantly instead of re-parsing/re-embedding every paper.
    
    If the on-disk index is corrupted (e.g. truncated files.zip from a
    previous race condition) the index directory is wiped and rebuilt fresh.
    """
    write_index_status(user_id, status="building")
    try:
        settings = get_user_settings(user_id)
        try:
            index = await get_directory_index(settings=settings)
        except Exception as first_err:
            # Detect corrupt index (zlib / zip errors) and recover
            err_msg = str(first_err).lower()
            if "decompress" in err_msg or "truncated" in err_msg or "zlib" in err_msg:
                index_dir = get_user_index_dir(user_id)
                logger.warning(
                    f"[PQA] Corrupt index detected for user {user_id}, "
                    f"cleaning {index_dir} and rebuilding..."
                )
                for child in index_dir.iterdir():
                    if child.is_dir() and child.name.startswith("pqa_index"):
                        shutil.rmtree(child, ignore_errors=True)
                index = await get_directory_index(settings=settings)
            else:
                raise

        # Pre-build and cache the Docs object so the first query is fast
        await _build_and_cache_docs(user_id, settings, index)

        write_index_status(user_id, status="ready")
        return index
    except Exception as exc:
        write_index_status(user_id, status="error", error=str(exc))
        raise


async def _build_and_cache_docs(
    user_id: Any, settings: Settings, index: SearchIndex
) -> None:
    """Build a Docs object from the index and pickle it to disk.

    Skips the build if the on-disk cache is already up-to-date.
    """
    from paperqa import Docs

    index_files = await index.index_files
    if not index_files:
        clear_docs_cache(user_id)
        return

    revision = _compute_revision(index_files)

    # Check if already up-to-date on disk
    existing = load_docs_from_disk(user_id, revision)
    if existing is not None:
        logger.info(f"[PQA] Docs disk-cache already up-to-date for user {user_id}.")
        return

    logger.info(
        f"[PQA] Pre-building Docs object for user {user_id} "
        f"({len(index_files)} files)..."
    )
    docs = Docs()
    paper_directory = settings.agent.index.paper_directory
    for file_path in index_files.keys():
        full_path = paper_directory / file_path
        if full_path.exists():
            await docs.aadd(full_path, settings=settings)

    save_docs_to_disk(user_id, docs, revision)
    logger.info(f"[PQA] Docs pre-build complete for user {user_id}.")


def build_user_index_sync(user_id: Any) -> SearchIndex:
    """Synchronous wrapper for build_user_index.
    
    Use this from synchronous code (e.g., OpenInterpreter, FastAPI BackgroundTasks).
    A per-user lock serialises index builds so only one runs at a time,
    preventing concurrent writes that can corrupt the on-disk index.
    
    If a build is already running, this call **waits** for it to finish and
    then runs another build to pick up any papers that arrived in the meantime.
    This avoids the problem where a second upload's build is skipped and
    the disk Docs cache ends up stale.  Redundant builds are cheap because
    get_directory_index and _build_and_cache_docs both short-circuit when
    nothing has changed.
    """
    lock = _get_user_lock(user_id)
    lock.acquire()  # blocking — waits for any in-progress build to finish
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, build_user_index(user_id))
                return future.result()
        else:
            return asyncio.run(build_user_index(user_id))
    finally:
        lock.release()