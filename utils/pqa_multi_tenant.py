import asyncio
import os
import json
from pathlib import Path
from typing import Any

from paperqa import Settings
from paperqa.settings import AgentSettings, IndexSettings
from paperqa.agents.search import get_directory_index, SearchIndex

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


def get_user_settings(user_id: Any) -> Settings:
    """Build a PaperQA Settings object for the user.
    
    This loads the base configuration from pqa_settings.json and applies
    user-specific paper and index directories.
    """
    # Ensure JSON config and directories exist, returns path to user settings
    user_settings_path = ensure_user_pqa_settings(user_id)
    
    user_papers_dir = get_user_papers_dir(user_id)
    user_index_dir = get_user_index_dir(user_id)
    
    # Load settings from the user-specific JSON (which includes base settings + user paths)
    settings_data: dict = {}
    if user_settings_path.exists():
        try:
            settings_data = json.loads(user_settings_path.read_text())
        except Exception:
            settings_data = {}
    
    # Extract nested settings for proper object construction
    answer_data = settings_data.pop("answer", None)
    parsing_data = settings_data.pop("parsing", None)
    prompts_data = settings_data.pop("prompts", None)
    agent_data = settings_data.pop("agent", None)
    
    # Build nested settings objects
    from paperqa.settings import AnswerSettings, ParsingSettings, PromptSettings, MultimodalOptions
    
    # Handle multimodal enum conversion in parsing
    if parsing_data and "multimodal" in parsing_data:
        multimodal_str = parsing_data["multimodal"]
        if isinstance(multimodal_str, str):
            # Convert string to enum
            multimodal_map = {
                "off": MultimodalOptions.OFF,
                "on": MultimodalOptions.ON,
                "on_without_enrichment": MultimodalOptions.ON_WITHOUT_ENRICHMENT,
            }
            parsing_data["multimodal"] = multimodal_map.get(
                multimodal_str.lower(), 
                MultimodalOptions.OFF
            )
        # Remove reader_config if present (not a valid ParsingSettings field)
        parsing_data.pop("reader_config", None)
    
    answer_settings = AnswerSettings(**answer_data) if answer_data else None
    parsing_settings = ParsingSettings(**parsing_data) if parsing_data else None
    prompts_settings = PromptSettings(**prompts_data) if prompts_data else None
    
    # Build agent settings with user-specific paths
    agent_settings = None
    if agent_data:
        agent_index = agent_data.pop("index", {}) or {}
        # Remove potentially unsupported fields from agent_data
        agent_data.pop("agent_llm_config", None)  # LiteLLM handles this via environment
        
        # Override paths with user-specific directories
        agent_index["paper_directory"] = user_papers_dir
        agent_index["index_directory"] = user_index_dir
        agent_index["use_absolute_paper_directory"] = False
        agent_index["sync_with_paper_directory"] = True
        agent_index["recurse_subdirectories"] = False
        
        index_settings = IndexSettings(**agent_index)
        agent_settings = AgentSettings(index=index_settings, **agent_data)
    else:
        agent_settings = AgentSettings(
            index=IndexSettings(
                paper_directory=user_papers_dir,
                index_directory=user_index_dir,
                use_absolute_paper_directory=False,
                sync_with_paper_directory=True,
                recurse_subdirectories=False,
            ),
        )
    
    # Filter out fields that might not be directly supported by Settings constructor
    # These are handled separately or not needed
    unsupported_fields = {
        "llm_config",      # LiteLLM handles this via environment
        "embedding_config", # Not directly supported
        "manifest_file",    # Usually None
    }
    filtered_settings = {
        k: v for k, v in settings_data.items() 
        if v is not None and k not in unsupported_fields
    }
    
    # Build the main Settings object with all configuration
    settings = Settings(
        paper_directory=user_papers_dir,
        index_directory=user_index_dir,
        agent=agent_settings,
        **({k: v for k, v in {
            "answer": answer_settings,
            "parsing": parsing_settings,
            "prompts": prompts_settings,
        }.items() if v is not None}),
        **filtered_settings,
    )
    return settings


async def build_user_index(user_id: Any) -> SearchIndex:
    """Build/reuse the user's PaperQA index using Python API.
    
    This replaces the subprocess-based CLI approach with direct Python calls.
    The index is persisted to disk and reused on subsequent calls.
    """
    settings = get_user_settings(user_id)
    return await get_directory_index(settings=settings)


def build_user_index_sync(user_id: Any) -> SearchIndex:
    """Synchronous wrapper for build_user_index.
    
    Use this from synchronous code (e.g., OpenInterpreter).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    
    if loop and loop.is_running():
        # We're in an async context, create a new thread to run the coroutine
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, build_user_index(user_id))
            return future.result()
    else:
        return asyncio.run(build_user_index(user_id))