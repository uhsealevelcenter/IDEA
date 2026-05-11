import asyncio
import logging
import shutil
from time import time

from interpreter.core.core import OpenInterpreter
from sqlmodel import Session

from backend.auth import get_current_user
from backend.state import (
    interpreter_instances,
    redis_client,
    LAST_ACTIVE_PREFIX,
    IDLE_TIMEOUT,
    STATIC_DIR,
)
from core.config import settings
from utils.prompts.system_prompt import sys_prompt
from utils.tools.custom_functions import custom_tool
from utils.prompt_manager import get_prompt_manager

logger = logging.getLogger(__name__)


class InterpreterError(Exception):
    """Custom exception for interpreter-related errors"""
    pass


def get_or_create_interpreter(session_key: str, token: str | None = None, db: Session | None = None) -> OpenInterpreter:
    """Get existing interpreter or create new one. If token+db provided, use per-user active prompt."""
    try:
        if session_key in interpreter_instances:
            logger.info(f"Retrieved existing interpreter for session {session_key}")
            return interpreter_instances[session_key]

        interpreter = OpenInterpreter()

        active_prompt = ""
        user = None
        if token and db is not None:
            user = get_current_user(token)
            if user:
                active_prompt = get_prompt_manager().get_active_prompt(db, user.id)
        if not active_prompt and (token and db and user):
            active_prompt = get_prompt_manager().get_active_prompt(db, user.id)
        interpreter.system_message = sys_prompt + active_prompt

        interpreter.llm.supports_vision = True

        ## OpenAI Models
        interpreter.llm.model = "gpt-5.4-nano" # "Reasoning" model
        #interpreter.llm.model = "gpt-5.5-2026-04-23" # "Reasoning" model
        #interpreter.llm.model = "gpt-5.4-2026-03-05" # "Reasoning" model
        #interpreter.llm.model = "gpt-5.2-2025-12-11" # "Reasoning" model
        #interpreter.llm.model = "gpt-5.1-2025-11-13" # "Reasoning" model
        #interpreter.llm.model = "gpt-5-2025-08-07" # "Reasoning" model
        #interpreter.llm.model = "gpt-4.1-2025-04-14" # "Intelligence" model
        #interpreter.llm.model = "gpt-4o-2024-11-20" # "Intelligence" model
        # interpreter.llm.model = "gpt-4o"
        interpreter.llm.supports_functions = True

        ## Jetstream2 Models (https://docs.jetstream-cloud.org/inference-service/api/)
        # interpreter.llm.api_key = os.getenv("JETSTREAM2_API_KEY")
        # interpreter.llm.api_base = "https://llm.jetstream-cloud.org/api"
        # interpreter.llm.model = "openai/DeepSeek-R1"
        # interpreter.llm.model = "openai/llama-4-scout"
        # interpreter.llm.model = "openai/Llama-3.3-70B-Instruct"
        # interpreter.llm.supports_functions = False

        ## Specific settings for LLMs
        # Reasoning models (e.g, GPT5+)
        interpreter.llm.reasoning_effort = "medium"
        #interpreter.llm.reasoning_effort = "low" # GPT-5.1 "none" | "low" | "medium" | "high"
        #interpreter.llm.reasoning_effort = "minimal" # GPT-5 "minimal" | "low" | "medium" | "high"
        interpreter.llm.temperature = 0.2
        interpreter.llm.context_window = 400000
        interpreter.llm.max_completion_tokens = 64000

        # # Intelligence models (e.g., GPT4.1)
        # interpreter.llm.temperature = 0.2
        # interpreter.llm.context_window = 128000
        # interpreter.llm.context_window = 1047576
        # interpreter.llm.max_tokens = 16383

        ## LiteLLM proxy routing (tracks spend, virtual keys, fallbacks)
        if settings.LITELLM_PROXY_URL:
            interpreter.llm.api_base = settings.LITELLM_PROXY_URL
            interpreter.llm.api_key = settings.LITELLM_MASTER_KEY

        ## General settings for computer interpreter
        #interpreter.max_output = 16383
        interpreter.max_output = 64000
        interpreter.computer.import_computer_api = False
        interpreter.computer.run("python", custom_tool)
        interpreter.auto_run = True

        interpreter_instances[session_key] = interpreter
        logger.info(f"Created new interpreter for session {session_key}")
        return interpreter
    except Exception as e:
        logger.error(f"Error creating interpreter for session {session_key}: {str(e)}")
        raise


def clear_session(session_key: str):
    """Clear all resources associated with a session"""
    try:
        interpreter = interpreter_instances.get(session_key)
        if interpreter:
            interpreter.reset()
            del interpreter_instances[session_key]

        redis_client.delete(f"{LAST_ACTIVE_PREFIX}{session_key}")
        redis_client.delete(f"messages:{session_key}")

        try:
            user_id, raw_session_id = session_key.split(":", 1)
            session_dir = STATIC_DIR / user_id / raw_session_id
            if session_dir.exists():
                shutil.rmtree(session_dir)
        except ValueError:
            raw_session_id = session_key
            session_dir = STATIC_DIR / raw_session_id
            if session_dir.exists():
                shutil.rmtree(session_dir)
        logger.info(f"Cleared session {session_key}")
    except Exception as e:
        logger.error(f"Error clearing session {session_key}: {str(e)}")
        raise


def clear_all_interpreter_instances():
    """Clear all interpreter instances to force recreation with new system message"""
    try:
        for session_key, interpreter in list(interpreter_instances.items()):
            try:
                interpreter.reset()
                logger.info(f"Reset interpreter for session {session_key}")
            except Exception as e:
                logger.error(f"Error resetting interpreter for session {session_key}: {str(e)}")

        interpreter_instances.clear()
        logger.info("Cleared all interpreter instances due to system prompt change")
    except Exception as e:
        logger.error(f"Error clearing all interpreter instances: {str(e)}")
        raise


async def cleanup_idle_sessions():
    """Remove interpreter instances and data for idle sessions"""
    try:
        current_time = time()
        logger.info(f"Current time: {current_time}")
        logger.info(f"interpreter_instances: {list(interpreter_instances.keys())}")
        for session_key in list(interpreter_instances.keys()):
            try:
                last_active = redis_client.get(f"{LAST_ACTIVE_PREFIX}{session_key}")
                if last_active:
                    logger.info(f"Last active time for session {session_key}: {last_active}")
                    last_active_time = float(last_active.decode('utf-8'))
                    if current_time - last_active_time > IDLE_TIMEOUT:
                        clear_session(session_key)
            except Exception as e:
                logger.error(f"Error during idle cleanup for {session_key}: {str(e)}")
    except Exception as e:
        logger.error(f"Error cleaning up sessions: {str(e)}")
        raise
