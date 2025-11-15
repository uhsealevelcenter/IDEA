import os
import json
import subprocess
from pathlib import Path
from typing import Any

# Roots derived from environment
PQA_HOME = Path(os.getenv("PQA_HOME", "/app/data"))
PQA_ROOT = PQA_HOME / ".pqa"
SETTINGS_DIR = PQA_ROOT / "settings"
INDEXES_ROOT = PQA_ROOT / "indexes"
PAPERS_ROOT = Path(os.getenv("PAPER_DIRECTORY", "/app/data/papers"))


def get_user_papers_dir(user_id: Any) -> Path:
    return PAPERS_ROOT / str(user_id)


def get_user_index_dir(user_id: Any) -> Path:
    return INDEXES_ROOT / str(user_id)


def get_user_settings_path(user_id: Any) -> Path:
    return SETTINGS_DIR / f"user_{user_id}.json"


def get_user_settings_name(user_id: Any) -> str:
    # PaperQA CLI expects a name without extension; it will append .json
    return f"user_{user_id}"


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


def _has_built_index(user_id: Any) -> bool:
    """Detect whether at least one current index exists and contains metadata for this user."""
    user_index_dir = get_user_index_dir(user_id)
    if not user_index_dir.exists():
        return False
    for child in user_index_dir.iterdir():
        if child.is_dir() and child.name.startswith("pqa_index_"):
            index_meta = child / "index" / "meta.json"
            files_zip = child / "files.zip"
            if index_meta.exists() and files_zip.exists():
                return True
    return False


def ensure_user_index_built(user_id: Any) -> None:
    """If the per-user index is missing, build it once via the PaperQA CLI."""
    if _has_built_index(user_id):
        return
    # Build the index with the CLI to guarantee on-disk structures
    settings_name = get_user_settings_name(user_id)
    papers_dir = str(get_user_papers_dir(user_id))
    try:
        subprocess.run(
            ["/opt/venv/bin/pqa", "-s", settings_name, "index", papers_dir],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception as e:
        # Best-effort: leave to runtime build if CLI fails
        # You can add logging here if desired
        pass 