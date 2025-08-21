import os
import json
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
    index["use_absolute_paper_directory"] = True
    index["sync_with_paper_directory"] = True

    agent["index"] = index
    settings_data["agent"] = agent

    # Write the user-specific settings
    user_settings_path.write_text(json.dumps(settings_data, indent=2))
    return user_settings_path 