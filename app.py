import asyncio
import json
from math import ceil
import os
from datetime import date, datetime, timedelta
from time import time
import logging
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List
import hashlib
import secrets
from uuid import UUID
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pathlib import Path
from fastapi import UploadFile, File
from utils.custom_functions import custom_tool
import redis
from starlette.middleware.base import BaseHTTPMiddleware
from interpreter.core.core import OpenInterpreter
from slowapi.errors import RateLimitExceeded
import models
from models import LoginRequest, LoginResponse, PromptCreateRequest, PromptUpdateRequest, PromptResponse, \
    PromptListResponse, SetActivePromptRequest, UpdatePassword, UserUpdate, UserCreate, UserPublic, GenericMessage, User
import crud
from core.security import verify_password as verify_password_hash
from sqlalchemy.exc import IntegrityError
# import magic
# import subprocess # For download_conversation (Puppeteer version, under development)

## Required for audio transcription
# from openai import OpenAI # Uncomment if using OpenAI Whisper API instead of LiteLLM
from litellm import transcription, completion  # LiteLLM for audio transcription & tool planning
import litellm

# Set longer timeout for LiteLLM to handle long-running MCP tool calls
litellm.request_timeout = 600  # 10 minutes timeout for API requests

from utils.transcription_prompt import \
    transcription_prompt  # Transcription prompt for Generic IDEA example (abbreviations, etc.)
from utils.custom_instructions import get_custom_instructions  # Generic Assistant (Custom Instructions)
#from utils.custom_instructions_ClimateIndices import get_custom_instructions  # Climate Assistant

# Import prompt manager
from utils.prompt_manager import init_prompt_manager, get_prompt_manager
from knowledge_base_routes import router as knowledge_base_router
from conversation_routes import router as conversation_router
from mcp_routes import router as mcp_router
from sqlmodel import Session
from auth import (
    generate_auth_token, verify_password, is_authenticated, get_auth_token,
    add_auth_session, remove_auth_session, SESSION_TIMEOUT, get_db, get_current_user
)

from utils.system_prompt import sys_prompt # New (for reasoning LLMs, like GPT-5), also contains Open Interpreter prompt
from utils.pqa_multi_tenant import ensure_user_pqa_settings, ensure_user_index_built
from core.mcp_manager import mcp_manager

import interpreter.core.llm.llm as llm_mod


# mcp_tools.py is now a static module that queries the database directly
# No code generation needed



# LOG_DIR = Path("logs")
# LOG_DIR.mkdir(parents=True, exist_ok=True)
# LOG_FILE = LOG_DIR / "idea.log"

# logging.basicConfig(
#     level=logging.DEBUG,
#     format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
#     handlers=[
#         RotatingFileHandler(str(LOG_FILE), maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"),
#         logging.StreamHandler()
#     ],
# )
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Inject reasoning_effort at the completions layer (affects both text + tool paths)
_orig_completions = llm_mod.fixed_litellm_completions

def fixed_litellm_completions_with_reasoning(**params):
    p = dict(params)
    p.setdefault("reasoning_effort", "low")
    # Optionally: also enforce a cap on generated tokens for reasoning models
    p.setdefault("max_completion_tokens", 64000)
    yield from _orig_completions(**p)

llm_mod.fixed_litellm_completions = fixed_litellm_completions_with_reasoning

# Keep function-level monkeypatch (optional now that completions is wrapped)
_orig_text = llm_mod.run_text_llm

def run_text_llm_with_reasoning(self, params):
    p = dict(params)
    p.setdefault("reasoning_effort", "low")
    return _orig_text(self, p)

llm_mod.run_text_llm = run_text_llm_with_reasoning

# If you also need tool-calling:
_orig_tool = llm_mod.run_tool_calling_llm

def run_tool_calling_llm_with_reasoning(self, params):
    p = dict(params)
    p.setdefault("reasoning_effort", "low")
    return _orig_tool(self, p)

llm_mod.run_tool_calling_llm = run_tool_calling_llm_with_reasoning

MCP_TOOL_PLANNER_PROMPT = (
    "You are a routing assistant for the IDEA application. "
    "Analyze the latest user message and decide whether calling one of the available MCP tools would help. "
    "Only call a tool if it is likely to provide data needed to answer the user. "
    "Otherwise, do not call any tool."
)


async def gather_available_mcp_tools(db: Session):
    """Retrieve active MCP connections and their tool schemas."""
    connections = crud.list_active_mcp_connections(session=db)
    tool_defs = []
    tool_lookup: dict[str, tuple[models.MCPConnection, dict[str, Any]]] = {}

    for connection in connections:
        if not connection.is_active:
            continue
        try:
            tools_payload = await mcp_manager.list_tools(connection)
        except Exception as exc:  # pragma: no cover - dependent service
            logger.warning("Failed to list tools for connection %s: %s", connection.id, exc)
            continue

        tools = (
            tools_payload.get("tools")
            if isinstance(tools_payload, dict)
            else tools_payload
        ) or []

        for tool in tools:
            tool_name = tool.get("name")
            if not tool_name:
                continue
            tool_id = f"mcp_{connection.id.hex}_{tool_name}".replace("-", "_")
            raw_schema = (
                tool.get("inputSchema")
                or tool.get("input_schema")
                or {"type": "object", "properties": {}}
            )
            parameters = (
                raw_schema
                if isinstance(raw_schema, dict)
                else {"type": "object", "properties": {}}
            )
            tool_defs.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_id,
                        "description": f"[{connection.name}] {tool.get('description', '')}".strip(),
                        "parameters": parameters,
                    },
                }
            )
            tool_lookup[tool_id] = (connection, tool)

    return tool_defs, tool_lookup


def _pretty_json(data: Any, max_length: int = 4000) -> str:
    try:
        text = json.dumps(data, indent=2, ensure_ascii=False)
    except Exception:  # pragma: no cover - fallback
        text = str(data)
    if len(text) > max_length:
        return text[: max_length - 3] + "..."
    return text


def _generate_mcp_wrapper_code(tool_defs: list[dict], tool_lookup: dict, db: Session) -> str:
    """Generate Python code that defines MCP tool wrapper functions."""
    imports = """
import asyncio
import json
from uuid import UUID
from core.mcp_manager import mcp_manager
from models import MCPConnection
from sqlmodel import Session
from core.db import engine

# MCP connection and tool lookup (connection_id, tool_name)
_mcp_lookup = {}
"""

    functions_code = []
    for tool_def in tool_defs:
        func_spec = tool_def.get("function", {})
        tool_id = func_spec.get("name")
        if not tool_id or tool_id not in tool_lookup:
            continue

        connection, tool = tool_lookup[tool_id]

        # Store connection and tool info in a global dict
        imports += f"_mcp_lookup['{tool_id}'] = ({connection.id!r}, {tool['name']!r})\n"

        # Generate function signature from parameters
        params = func_spec.get("parameters", {}).get("properties", {})
        required = func_spec.get("parameters", {}).get("required", [])

        param_list = []
        for param_name, param_spec in params.items():
            if param_name in required:
                param_list.append(param_name)
            else:
                default_val = param_spec.get("default", "None")
                if isinstance(default_val, str):
                    default_val = f"'{default_val}'"
                param_list.append(f"{param_name}={default_val}")

        params_str = ", ".join(param_list) if param_list else ""

        # Generate function code
        func_code = f'''
def {tool_id}({params_str}):
    """
    {func_spec.get("description", "MCP tool")}
    """
    conn_id, tool_name = _mcp_lookup['{tool_id}']

    # Get connection from database
    with Session(engine) as session:
        connection = session.get(MCPConnection, conn_id)
        if not connection:
            return {{"error": "MCP connection not found"}}

        # Build arguments dict
        arguments = {{}}
'''

        # Add argument collection
        for param_name in params.keys():
            func_code += f'        if {param_name} is not None:\n'
            func_code += f'            arguments["{param_name}"] = {param_name}\n'

        func_code += f'''
        # Run async call
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        async def _call():
            result = await mcp_manager.call_tool(connection, tool_name, arguments)
            return result

        result = loop.run_until_complete(_call())
        print(f"\\n🔧 Called MCP tool: {tool_id}")
        print(f"Arguments: {{arguments}}")
        print(f"Result: {{result}}\\n")
        return result
'''
        functions_code.append(func_code)

    return imports + "\n".join(functions_code)


async def plan_and_run_mcp_tools(
    *,
    interpreter: OpenInterpreter,
    user_message: str,
    db: Session,
) -> list[dict[str, Any]]:
    """Let an LLM decide whether to call MCP tools and execute them."""
    if not user_message.strip():
        return []

    tool_defs, tool_lookup = await gather_available_mcp_tools(db)
    if not tool_defs:
        return []

    try:
        planner_response = await asyncio.to_thread(
            completion,
            model=interpreter.llm.model,
            messages=[
                {"role": "system", "content": MCP_TOOL_PLANNER_PROMPT},
                {"role": "user", "content": user_message},
            ],
            tools=tool_defs,
            tool_choice="auto",
        )
    except Exception as exc:  # pragma: no cover - upstream failure
        logger.warning("MCP tool planner failed: %s", exc)
        return []

    message = planner_response["choices"][0]["message"]
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        return []

    executed_tools: list[dict[str, Any]] = []
    for call in tool_calls:
        fn = call.get("function") or {}
        tool_id = fn.get("name")
        if not tool_id or tool_id not in tool_lookup:
            continue
        connection, tool = tool_lookup[tool_id]
        arguments_raw = fn.get("arguments") or "{}"
        try:
            arguments = json.loads(arguments_raw)
        except json.JSONDecodeError:
            arguments = {}

        try:
            result = await mcp_manager.call_tool(connection, tool["name"], arguments)
        except Exception as exc:
            logger.error("MCP tool %s execution failed: %s", tool_id, exc)
            result = {"error": str(exc)}

        executed_tools.append(
            {
                "connection": connection,
                "tool": tool,
                "arguments": arguments,
                "result": result,
            }
        )

        summary_text = (
            f"MCP tool {tool['name']} from connection '{connection.name}' returned:\n"
            f"{_pretty_json(result)}"
        )
        interpreter.messages.append({"role": "assistant", "content": summary_text})

    return executed_tools

IDLE_TIMEOUT = 3600  # 1 hour in seconds
INTERPRETER_PREFIX = "interpreter:"
LAST_ACTIVE_PREFIX = "last_active:"
CLEANUP_INTERVAL = 1800  # Run cleanup every 30 minutes

# Constants for file upload
STATIC_DIR = Path("static")
UPLOAD_DIR = Path("uploads")
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {'.csv', '.txt', '.json', '.nc', '.xlsx', '.mat', '.tif', '.png', '.jpg'}  # Added PNG, JPG. & MAT

# Rate limiting
UPLOAD_RATE_LIMIT = "5/minute"
MAX_UPLOADS_PER_SESSION = 10  # Maximum files per session
CLAMD_HOST = "localhost"  # Docker service name
CLAMD_PORT = 3310
CHAT_RATE_LIMIT = "10/minute"

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)


class InterpreterError(Exception):
    """Custom exception for interpreter-related errors"""
    pass


today = date.today()
root_path = "/idea-api"
host = (
    os.getenv("API_HOST", "https://uhslc.soest.hawaii.edu/idea-api")
)

app = FastAPI(root_path=root_path)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount('/' + str(STATIC_DIR), StaticFiles(directory=STATIC_DIR), name="static")
# Serve frontend assets (CSS/JS) for shared pages under a stable, prefixed path
app.mount('/assets', StaticFiles(directory='frontend'), name='assets')

init_prompt_manager()

app.include_router(knowledge_base_router)
app.include_router(conversation_router, prefix="/conversations", tags=["conversations"])
app.include_router(mcp_router)

# Get CORS origins from environment variable or use defaults
cors_origins_env = os.getenv("CORS_ORIGINS", "")
if cors_origins_env:
    origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
else:
    # Default origins if environment variable is not set
    origins = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
        "http://localhost",
        "*",
        "http://172.18.46.161",
        "http://172.18.46.161:8001",
        "https://uhslc.soest.hawaii.edu/research/IDEA",
    ]


# TODO:
# ALLOWED_MIME_TYPES = {
#     'text/plain',
#     'application/pdf',
#     'application/json',
#     'text/csv'
# }

# Add request size limit middleware
class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        #if request.method == "POST":
        if request.method == "POST" and request.url.path.endswith("/upload"): # Limits to uploads, so images in chat are unlimited

            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > MAX_FILE_SIZE:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request too large"}
                )
        return await call_next(request)


app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Custom rate limit exceeded handler"""
    print(f"Rate limit exceeded: {exc}")
    # if exc has attribute retry_after, then use it
    retry_after = getattr(exc, "retry_after", None)
    if retry_after:
        message = f"Too many requests. Please try again in {retry_after} seconds."
    else:
        message = "Too many requests. Please try again later."

    return JSONResponse(
        status_code=429,
        content={
            "detail": message,
            # "retry_after": exc.retry_after  # Seconds until next request is allowed
        }
    )


def make_session_key(user_id: str | int, session_id: str) -> str:
    return f"{user_id}:{session_id}"


async def scan_file(file_path: Path) -> tuple[bool, str]:
    """Scan a file for viruses using ClamAV"""
    # TODO: Not implemented yet
    # try:
    #     # Ping ClamAV to ensure it's responsive
    #     cd = clamd.ClamdUnixSocket()
    #     cd.ping()
    #     logger.info(f"ClamAV ping successful")

    #     # Perform the scan
    #     logger.info(f"Scanning file: {file_path}")
    #     result = cd.scan(str(file_path))
    #     logger.info(f"ClamAV scan result: {result}")

    #     if not result:
    #         return False, "Scan failed: No result from ClamAV"

    #     file_result = result.get(str(file_path))

    #     if file_result == "OK":
    #         logger.info(f"File {file_path} is clean")
    #         return True, "File is clean"
    #     else:
    #         return False, f"Potential threat detected: {file_result}"

    # except clamd.ConnectionError as ce:
    #     logger.error(f"ClamAV connection error: {ce}")
    #     return True, "Virus scan skipped (ClamAV unavailable)"
    return True, "Virus scan skipped (ClamAV unavailable)"


async def check_session_upload_limit(user_id: str, session_id: str) -> bool:
    """Check if session has reached upload limit"""
    session_dir = STATIC_DIR / str(user_id) / session_id / UPLOAD_DIR
    if not session_dir.exists():
        return True

    file_count = sum(1 for _ in session_dir.glob("*") if _.is_file())
    return file_count < MAX_UPLOADS_PER_SESSION


redis_client = redis.Redis(host="redis", port=6379, db=0)
# Global dictionary to store interpreter instances
# Not thread safe, but should be ok for proof of concept
interpreter_instances: Dict[str, OpenInterpreter] = {}



# Authentication endpoints
@app.post("/login", response_model=LoginResponse)
async def login(login_request: LoginRequest, session: Session = Depends(get_db)):
    """Login endpoint to authenticate users"""
    user = verify_password(login_request.username, login_request.password, session)
    if user:
        token = generate_auth_token()
        expiry_time = datetime.now() + timedelta(seconds=SESSION_TIMEOUT)
        add_auth_session(token, user.id, expiry_time)

        return LoginResponse(
            success=True,
            token=token,
            message="Login successful"
        )
    else:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )


@app.post("/logout")
async def logout(token: str = Depends(get_auth_token)):
    """Logout endpoint to invalidate authentication token"""
    remove_auth_session(token)
    return {"message": "Logged out successfully"}


@app.get("/auth/verify")
async def verify_auth(token: str = Depends(get_auth_token)):
    """Verify if current authentication token is valid"""
    return {"authenticated": True, "message": "Token is valid"}


@app.get("/users/me")
async def get_current_user_profile(token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    """Get current authenticated user's profile information"""
    try:
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
            
        # Re-fetch user from database to get latest info
        db_user = crud.get_user_by_id(session=db, user_id=user.id)
        if db_user is None:
            raise HTTPException(status_code=404, detail="User not found")
            
        return {
            "id": str(db_user.id),
            "email": db_user.email,
            "full_name": db_user.full_name,
            "is_active": db_user.is_active,
            "is_superuser": db_user.is_superuser,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user profile: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get user profile")


@app.get("/share/{share_token}")
async def shared_conversation_page(share_token: str):
    """Serve the shared conversation page"""
    frontend_dir = Path(__file__).parent / "frontend"
    share_html_path = frontend_dir / "share.html"
    
    if not share_html_path.exists():
        raise HTTPException(status_code=404, detail="Share page not found")
    
    return FileResponse(share_html_path, media_type="text/html")


# Account management endpoints
@app.post("/users/change-password")
async def change_password(payload: UpdatePassword, token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    """Change password for the current authenticated user"""
    try:
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        # Re-fetch user in this DB session to avoid detached instance issues
        db_user = crud.get_user_by_id(session=db, user_id=user.id)
        if db_user is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Verify current password
        if not verify_password_hash(payload.current_password, db_user.hashed_password):
            raise HTTPException(status_code=400, detail="Current password is incorrect")

        # Update to new password
        crud.update_user(session=db, db_user=db_user, user_in=UserUpdate(password=payload.new_password))
        return {"message": "Password updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error changing password: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to change password")


def _ensure_superuser(token: str) -> User:
    user = get_current_user(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return user


@app.get("/users", response_model=List[UserPublic])
async def list_users(token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    """List all users (superuser only)"""
    try:
        _ensure_superuser(token)
        users = crud.list_users(session=db)
        return [UserPublic.model_validate(user, from_attributes=True) for user in users]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing users: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to list users")


@app.post("/users", response_model=UserPublic, status_code=201)
async def create_user_admin(user_in: UserCreate, token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    """Create a new user (superuser only)"""
    try:
        _ensure_superuser(token)
        try:
            db_user = crud.create_user(session=db, user_create=user_in)
        except IntegrityError:
            raise HTTPException(status_code=400, detail="A user with this email already exists")
        return UserPublic.model_validate(db_user, from_attributes=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating user: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create user")


@app.put("/users/{user_id}", response_model=UserPublic)
async def update_user_admin(user_id: UUID, user_in: UserUpdate, token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    """Update an existing user (superuser only)"""
    try:
        _ensure_superuser(token)
        db_user = crud.get_user_by_id(session=db, user_id=user_id)
        if db_user is None:
            raise HTTPException(status_code=404, detail="User not found")
        try:
            updated_user = crud.update_user(session=db, db_user=db_user, user_in=user_in)
        except IntegrityError:
            raise HTTPException(status_code=400, detail="A user with this email already exists")
        return UserPublic.model_validate(updated_user, from_attributes=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update user")


@app.delete("/users/{user_id}", response_model=GenericMessage)
async def delete_user_admin(user_id: UUID, token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    """Delete a user (superuser only)"""
    try:
        admin = _ensure_superuser(token)
        if admin.id == user_id:
            raise HTTPException(status_code=400, detail="Superusers cannot delete their own account")
        db_user = crud.get_user_by_id(session=db, user_id=user_id)
        if db_user is None:
            raise HTTPException(status_code=404, detail="User not found")
        crud.delete_user(session=db, db_user=db_user)
        return GenericMessage(message="User deleted successfully")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete user")


# Prompt Management Endpoints
@app.get("/prompts", response_model=List[PromptListResponse])
async def list_prompts(token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    """List all available prompts for the current user"""
    try:
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        prompts = get_prompt_manager().list_prompts(db, user.id)
        return prompts
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing prompts: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to list prompts")

@app.get("/prompts/{prompt_id}", response_model=PromptResponse)
async def get_prompt(prompt_id: str, token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    """Get a specific prompt by ID for the current user"""
    try:
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        prompt = get_prompt_manager().get_prompt(db, user.id, prompt_id)
        if not prompt:
            raise HTTPException(status_code=404, detail="Prompt not found")
        return prompt
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting prompt {prompt_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get prompt")

@app.post("/prompts", response_model=PromptResponse)
async def create_prompt(prompt_data: PromptCreateRequest, token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    """Create a new prompt for the current user"""
    try:
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        new_prompt = get_prompt_manager().create_prompt(
            db,
            user.id,
            name=prompt_data.name,
            description=prompt_data.description,
            content=prompt_data.content
        )
        return new_prompt
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating prompt: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create prompt")

@app.put("/prompts/{prompt_id}", response_model=PromptResponse)
async def update_prompt(prompt_id: str, prompt_data: PromptUpdateRequest, token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    """Update an existing prompt for the current user"""
    try:
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        updated_prompt = get_prompt_manager().update_prompt(
            db,
            user.id,
            prompt_id=prompt_id,
            name=prompt_data.name,
            description=prompt_data.description,
            content=prompt_data.content
        )
        if not updated_prompt:
            raise HTTPException(status_code=404, detail="Prompt not found")
        return updated_prompt
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating prompt {prompt_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update prompt")

@app.delete("/prompts/{prompt_id}")
async def delete_prompt(prompt_id: str, token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    """Delete a prompt for the current user"""
    try:
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        success = get_prompt_manager().delete_prompt(db, user.id, prompt_id)
        if not success:
            raise HTTPException(status_code=404, detail="Prompt not found")
        return {"message": "Prompt deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting prompt {prompt_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete prompt")

@app.post("/prompts/set-active")
async def set_active_prompt(request: SetActivePromptRequest, token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    """Set a prompt as the active one for the current user"""
    try:
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        success = get_prompt_manager().set_active_prompt(db, user.id, request.prompt_id)
        if not success:
            raise HTTPException(status_code=404, detail="Prompt not found")

        # Clear all existing interpreter instances so they get recreated with the new system message
        clear_all_interpreter_instances()

        return {"message": "Active prompt set successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting active prompt {request.prompt_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to set active prompt")


def get_or_create_interpreter(session_key: str, token: str | None = None, db: Session | None = None) -> OpenInterpreter:
    """Get existing interpreter or create new one. If token+db provided, use per-user active prompt."""
    try:
        # Return existing instance if it exists
        if session_key in interpreter_instances:
            logger.info(f"Retrieved existing interpreter for session {session_key}")
            return interpreter_instances[session_key]

        # Create new interpreter instance with default settings
        interpreter = OpenInterpreter()

        # Get active system prompt from prompt manager
        active_prompt = ""
        user = None
        if token and db is not None:
            user = get_current_user(token)
            if user:
                active_prompt = get_prompt_manager().get_active_prompt(db, user.id)
        if not active_prompt and (token and db and user):
            # Fallback to previous file-backed default behavior for safety
            active_prompt = get_prompt_manager().get_active_prompt(db, user.id)
        interpreter.system_message = sys_prompt + active_prompt

        # Enable vision
        interpreter.llm.supports_vision = True

        ## OpenAI Models
        interpreter.llm.model = "gpt-5-2025-08-07" # "Reasoning" model
        #interpreter.llm.model = "gpt-4.1-2025-04-14" # "Intelligence" model
        #interpreter.llm.model = "gpt-4o-2024-11-20" # "Intelligence" model
        # interpreter.llm.model = "gpt-4o"
        interpreter.llm.supports_functions = True

        ## Jetstream2 Models (https://docs.jetstream-cloud.org/inference-service/api/)
        # interpreter.llm.api_key = os.getenv("JETSTREAM2_API_KEY") # api key to send your model 
        # interpreter.llm.api_base = "https://llm.jetstream-cloud.org/api" # add api base for OpenAI compatible provider
        # interpreter.llm.model = "openai/DeepSeek-R1" # add openai/ prefix to route as OpenAI provider
        # interpreter.llm.model = "openai/llama-4-scout" # add openai/ prefix to route as OpenAI provider    
        # interpreter.llm.model = "openai/Llama-3.3-70B-Instruct" # add openai/ prefix to route as OpenAI provider    
        # interpreter.llm.supports_functions = False  # Set to True if your model supports functions (optional)

        ## Specific settings for LLMs
        # Reasoning models (e.g, GPT5)
        # interpreter.llm.reasoning_effort = "high" # GPT-5 "minimal" | "low" | "medium" | "high"
        interpreter.llm.temperature = 0.2 # Temperature not used by reasoning models, set to default (e.g., GPT-5)
        interpreter.llm.context_window = 400000 # GPT-5 (max context window)
        # interpreter.llm.max_completion_tokens = 64000 # GPT-5 (128K, previously max_tokens, max tokens generated per request (prompt + max_completion_tokens can not exceed context_window)

        # # Intelligence models (e.g., GPT4.1)
        # interpreter.llm.temperature = 0.2 # Temperature (0-2, float) --> fairly deterministic
        # interpreter.llm.context_window = 128000 # Setting to maximum for gpt-4o as per documentation
        # interpreter.llm.context_window = 1047576 # Setting to maximum for gpt-4.1 as per documentation
        # interpreter.llm.max_tokens = 16383 # Max tokens generated per request (prompt + max_tokens can not exceed context_window)
        # #interpreter.llm.max_budget = 0.03 # Commented (depreciated?)
        
        ## General settings for computer interpreter
        #interpreter.max_output = 16383 # Max number of characters (not tokens) for code outputs (SEA web, GPT4.1)
        interpreter.max_output = 64000 # Max number of characters (not tokens) for code outputs (SEA local, GPT5)
        interpreter.computer.import_computer_api = False
        interpreter.computer.run("python", custom_tool)
        interpreter.auto_run = True

        # Store the instance
        interpreter_instances[session_key] = interpreter
        logger.info(f"Created new interpreter for session {session_key}")
        return interpreter
    except Exception as e:
        logger.error(f"Error creating interpreter for session {session_key}: {str(e)}")
        raise


async def periodic_cleanup():
    """Background task for periodic cleanup of idle sessions"""
    while True:
        try:
            logger.info("Running periodic cleanup of idle sessions")
            await cleanup_idle_sessions()
            await asyncio.sleep(CLEANUP_INTERVAL)
        except Exception as e:
            logger.error(f"Error in periodic cleanup: {str(e)}")
            await asyncio.sleep(60)  # Wait a minute before retrying if there's an error


@app.on_event("startup")
async def start_periodic_cleanup():
    """Start the periodic cleanup task when the app starts"""
    asyncio.create_task(periodic_cleanup())


@app.on_event("shutdown")
async def shutdown_resources():
    """Cleanup long-lived resources as the application stops."""
    await mcp_manager.close_all()


def clear_session(session_key: str):
    """Clear all resources associated with a session"""
    try:
        # Get interpreter instance
        interpreter = interpreter_instances.get(session_key)
        if interpreter:
            # Call reset() to properly terminate all languages and clean up
            interpreter.reset()
            # Remove from instances dict
            del interpreter_instances[session_key]

        # Clear Redis keys
        redis_client.delete(f"{LAST_ACTIVE_PREFIX}{session_key}")
        redis_client.delete(f"messages:{session_key}")

        # Remove session directory and all its contents (user_id/session_id structure)
        try:
            user_id, raw_session_id = session_key.split(":", 1)
            session_dir = STATIC_DIR / user_id / raw_session_id
            if session_dir.exists():
                import shutil
                shutil.rmtree(session_dir)
        except ValueError:
            # Fallback for old session keys without user_id
            raw_session_id = session_key
            session_dir = STATIC_DIR / raw_session_id
            if session_dir.exists():
                import shutil
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


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)}
    )


@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    try:
        # Save uploaded audio to a temp file
        contents = await file.read()
        temp_path = f"/tmp/{file.filename}"
        with open(temp_path, "wb") as f:
            f.write(contents)

        # # Call OpenAI Whisper API (gpt-4o)
        # client = OpenAI()
        # with open(temp_path, "rb") as audio_file:
        #     transcription_response = client.audio.transcriptions.create(
        #         #model="gpt-4o-transcribe",
        #         model="gpt-4o-mini-transcribe",
        #         file=audio_file
        #     )

        # LiteLLM alternative
        with open(temp_path, "rb") as audio_file:
            transcription_response = transcription(
                # model="gpt-4o-transcribe",
                model="gpt-4o-mini-transcribe",
                file=audio_file,
                prompt=transcription_prompt  # Optional prompt for transcription guidance (e.g., common abbreviations)
            )

        os.remove(temp_path)
        return {"text": transcription_response.text}

    except Exception as e:
        logger.error(f"Transcription error: {str(e)}")
        raise HTTPException(status_code=500, detail="Transcription failed")


@app.post("/chat")
@limiter.limit(CHAT_RATE_LIMIT)
async def chat_endpoint(request: Request, background_tasks: BackgroundTasks, token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    try:
        session_id = request.headers.get("x-session-id")
        if not session_id:
            raise HTTPException(status_code=400, detail="x-session-id header is required")

        body = await request.json()
        messages = body.get("messages", [])

        if not messages:
            raise HTTPException(status_code=400, detail="No messages provided")

        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        session_key = make_session_key(user.id, session_id)

        logger.info(f"Received messages for session {session_key}")
        interpreter = get_or_create_interpreter(session_key, token, db)

        pqa_settings_path = ensure_user_pqa_settings(user.id)
        pqa_settings_name = Path(str(pqa_settings_path)).stem
        # Build user index once if not present
        try:
            ensure_user_index_built(user.id)
        except Exception:
            pass

        # Gather MCP tools first so we can include them in custom instructions
        tool_defs = []
        tool_lookup = {}
        mcp_tool_descriptions = []
        try:
            tool_defs, tool_lookup = await gather_available_mcp_tools(db)
            if tool_defs:
                # Build descriptions for custom instructions
                for tool_def in tool_defs:
                    func_spec = tool_def.get("function", {})
                    tool_id = func_spec.get("name")
                    if tool_id and tool_id in tool_lookup:
                        connection, tool = tool_lookup[tool_id]
                        desc = func_spec.get("description", "No description")
                        params = func_spec.get("parameters", {}).get("properties", {})
                        param_list = ", ".join([f"{k} ({v.get('type', 'any')})" for k, v in params.items()])
                        mcp_tool_descriptions.append(
                            f"- {tool_id}({param_list}): {desc}"
                        )
                logger.info(f"Gathered {len(tool_defs)} MCP tools")
        except Exception as exc:
            logger.warning("Failed to gather MCP tools: %s", exc)

        station_id = '000'  # Placeholder
        interpreter.custom_instructions = get_custom_instructions(
            today=today,
            host=host,
            user_id=str(user.id),
            session_id=session_id,
            static_dir=STATIC_DIR,
            upload_dir=UPLOAD_DIR,
            station_id=station_id,
            pqa_settings_name=pqa_settings_name,
            mcp_tools=mcp_tool_descriptions,
        )

        redis_client.set(f"{LAST_ACTIVE_PREFIX}{session_key}", str(time()))

        # Update interpreter messages from any loaded conversation FIRST
        stored_messages = redis_client.get(f"messages:{session_key}")
        if stored_messages:
            try:
                interpreter.messages = json.loads(stored_messages)
                logger.info(f"Restored {len(interpreter.messages)} messages from Redis for session {session_key}")
            except Exception as e:
                logger.warning(f"Failed to restore messages from Redis: {str(e)}")

        # MCP tools are now available via mcp_tools.py (generated at startup and when connections change)
        # No need to regenerate on every chat request

        # Legacy pre-planning approach (can be removed if native integration works)
        tool_runs = []

        def event_stream():
            try:
                if tool_runs:
                    for run in tool_runs:
                        connection = run["connection"]
                        tool = run["tool"]
                        arguments = run["arguments"]
                        result_payload = run["result"]
                        message_text = (
                            f"🔧 MCP tool `{tool.get('name')}` from **{connection.name}**\n"
                            f"Arguments: {_pretty_json(arguments, max_length=1000)}\n\n"
                            f"Output:\n{_pretty_json(result_payload)}"
                        )
                        chunk = {
                            "start": True,
                            "end": True,
                            "role": "computer",
                            "type": "message",
                            "content": message_text,
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"

                for result in interpreter.chat(messages[-1], stream=True):
                    data = json.dumps(result) if isinstance(result, dict) else result
                    yield f"data: {data}\n\n"
            except Exception as e:
                logger.error(f"Error in chat stream: {str(e)}")
                error_message = {"error": str(e)}
                yield f"data: {json.dumps(error_message)}\n\n"
            finally:
                redis_client.set(
                    f"messages:{session_key}", json.dumps(interpreter.messages)
                )

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    except Exception as e:
        logger.error(f"Unexpected error in chat_endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/history")
def history_endpoint(request: Request, token: str = Depends(get_auth_token)):
    session_id = request.headers.get("x-session-id")
    if not session_id:
        return {"error": "x-session-id header is required"}
    user = get_current_user(token)
    if user is None:
        return {"error": "Invalid or expired token"}
    session_key = make_session_key(user.id, session_id)

    stored_messages = redis_client.get(f"messages:{session_key}")
    if stored_messages:
        return json.loads(stored_messages)
    return []


@app.post("/clear")
def clear_endpoint(request: Request, token: str = Depends(get_auth_token)):
    try:
        session_id = request.headers.get("x-session-id")
        if not session_id:
            raise HTTPException(status_code=400, detail="x-session-id header is required")
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        session_key = make_session_key(user.id, session_id)
        clear_session(session_key)
        return {"status": "Chat history cleared"}
    except redis.RedisError as e:
        logger.error(f"Redis error in clear_endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to clear chat history")
    except Exception as e:
        logger.error(f"Unexpected error in clear_endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/load-conversation")
async def load_conversation_endpoint(request: Request, token: str = Depends(get_auth_token), db: Session = Depends(get_db)):
    """Load a conversation's messages into the interpreter context"""
    try:
        session_id = request.headers.get("x-session-id")
        if not session_id:
            raise HTTPException(status_code=400, detail="x-session-id header is required")
        
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
            
        # Get request body
        body = await request.json()
        messages = body.get("messages", [])
        
        session_key = make_session_key(user.id, session_id)
        
        # Convert frontend message format to interpreter format
        interpreter_messages = []
        for msg in messages:
            # Skip console messages with active_line format as they cause issues
            if (msg.get("message_type") == "console" and 
                msg.get("message_format") == "active_line"):
                continue
                
            # Convert to format the interpreter expects (with required fields)
            if msg.get("role") in ["user", "assistant"]:
                # For user/assistant messages, use message type
                interpreter_msg = {
                    "role": msg.get("role"),
                    "type": "message",
                    "content": msg.get("content", "")
                }
                interpreter_messages.append(interpreter_msg)
            elif msg.get("role") == "computer":
                # For computer messages, convert to assistant with appropriate type
                msg_type = msg.get("message_type", "message")
                if msg_type == "console":
                    # Skip console messages entirely as they're not needed for context
                    continue
                else:
                    interpreter_msg = {
                        "role": "assistant",
                        "type": msg_type if msg_type in ["code", "message", "image"] else "message",
                        "content": msg.get("content", "")
                    }
                    if msg.get("message_format"):
                        interpreter_msg["format"] = msg.get("message_format")
                    interpreter_messages.append(interpreter_msg)
        
        # Store messages in Redis - the interpreter will load them on next chat request
        redis_client.set(
            f"messages:{session_key}", json.dumps(interpreter_messages)
        )
        
        # Clear any existing interpreter instance so it gets recreated with new messages
        if session_key in interpreter_instances:
            try:
                interpreter_instances[session_key].reset()
                del interpreter_instances[session_key]
                logger.info(f"Cleared existing interpreter for session {session_key}")
            except Exception as e:
                logger.warning(f"Error clearing existing interpreter: {str(e)}")
        
        logger.info(f"Stored {len(interpreter_messages)} messages in Redis for session {session_key}")
        return {"status": "Conversation loaded", "message_count": len(interpreter_messages)}
        
    except Exception as e:
        logger.error(f"Error loading conversation: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to load conversation: {str(e)}")


async def has_executable_header(file_path: Path) -> bool:
    """Check for executable file headers"""
    with open(file_path, "rb") as f:
        header = f.read(4)
        # Check for MZ header (Windows executables)
        if header.startswith(b'MZ'):
            return True
        # Check for ELF header (Linux executables)
        if header.startswith(b'\x7fELF'):
            return True
    return False


# mime = magic.Magic(mime=True)
@app.post("/upload")
@limiter.limit(UPLOAD_RATE_LIMIT)
async def upload_file(
        file: UploadFile = File(...),
        request: Request = None,
        token: str = Depends(get_auth_token)
):
    try:
        session_id = request.headers.get("x-session-id")
        if not session_id:
            raise HTTPException(status_code=400, detail="Session ID required")

        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        # Check session upload limit
        if not await check_session_upload_limit(str(user.id), session_id):
            raise HTTPException(
                status_code=429,
                detail=f"Upload limit reached. Maximum {MAX_UPLOADS_PER_SESSION} files per session"
            )

        # Create user/session upload directory if it doesn't exist
        session_dir = STATIC_DIR / str(user.id) / session_id / UPLOAD_DIR
        session_dir.mkdir(parents=True, exist_ok=True)

        # Validate file extension
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
            )

        # Save file to temporary location for scanning
        temp_file = session_dir / f"temp_{file.filename}"
        try:
            file_size = 0
            with temp_file.open("wb") as buffer:
                while chunk := await file.read(8192):
                    file_size += len(chunk)
                    if file_size > MAX_FILE_SIZE:
                        buffer.close()
                        temp_file.unlink()
                        raise HTTPException(
                            status_code=400,
                            detail=f"File too large. Maximum size: {MAX_FILE_SIZE / 1024 / 1024}MB"
                        )
                    buffer.write(chunk)

            # mime_type = mime.from_file(str(temp_file))
            # if mime_type not in ALLOWED_MIME_TYPES:
            #     temp_file.unlink()
            #     raise HTTPException(status_code=400, detail=f"File type {mime_type} not allowed")
            if await has_executable_header(temp_file):
                temp_file.unlink()
                raise HTTPException(status_code=400, detail="Executable file detected")
            # TODO: Scan file for viruses

            is_clean, scan_result = await scan_file(temp_file)
            if not is_clean:
                temp_file.unlink()
                raise HTTPException(status_code=400, detail=scan_result)

            # Move to final location
            final_path = session_dir / file.filename
            temp_file.rename(final_path)

            return {
                "filename": file.filename,
                "size": file_size,
                "path": str(final_path.relative_to(STATIC_DIR / str(user.id) / session_id / UPLOAD_DIR)),
                "scan_result": scan_result
            }

        except Exception as e:
            if temp_file.exists():
                temp_file.unlink()
            raise e

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/files/{filename}")
async def delete_file(filename: str, request: Request, token: str = Depends(get_auth_token)):
    try:
        session_id = request.headers.get("x-session-id")
        if not session_id:
            raise HTTPException(status_code=400, detail="Session ID required")

        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        file_path = STATIC_DIR / str(user.id) / session_id / UPLOAD_DIR / filename

        # Ensure the file exists and is within the session directory
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        # Verify the file is in the correct user/session directory
        try:
            file_path.relative_to(STATIC_DIR / str(user.id) / session_id / UPLOAD_DIR)
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied")

        # Delete the file
        file_path.unlink()

        return {"message": "File deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete file error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/files")
async def list_files(request: Request, token: str = Depends(get_auth_token)):
    try:
        session_id = request.headers.get("x-session-id")
        if not session_id:
            raise HTTPException(status_code=400, detail="Session ID required")

        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        session_dir = STATIC_DIR / str(user.id) / session_id / UPLOAD_DIR
        if not session_dir.exists():
            return []

        files = []
        for file_path in session_dir.glob("*"):
            if file_path.is_file():
                files.append({
                    "name": file_path.name,
                    "size": file_path.stat().st_size,
                    "path": str(file_path.relative_to(STATIC_DIR / str(user.id) / session_id / UPLOAD_DIR))
                })
        return files

    except Exception as e:
        logger.error(f"List files error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/files")
async def delete_all_files(request: Request, token: str = Depends(get_auth_token)):
    try:
        session_id = request.headers.get("x-session-id")
        if not session_id:
            raise HTTPException(status_code=400, detail="Session ID required")

        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        session_dir = STATIC_DIR / str(user.id) / session_id / UPLOAD_DIR
        if session_dir.exists():
            # Delete all files in the session directory
            for file_path in session_dir.glob("*"):
                if file_path.is_file():
                    file_path.unlink()

            # Optionally remove the directory itself
            session_dir.rmdir()

        return {"message": "All files deleted successfully"}

    except Exception as e:
        logger.error(f"Delete all files error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

## Alternative download_conversation function using Puppeteer (under development)
# @app.post("/downloadConversation")
# async def download_conversation(request: Request):
#     try:
#         body = await request.json()
#         print("Received /downloadConversation POST body:", body)

#         html_content = body.get("html")
#         generated_time = body.get("generatedTime", "Unknown time")

#         if not html_content:
#             print("No HTML content provided in request")
#             raise HTTPException(status_code=400, detail="No HTML content provided")

#         print("HTML Content length:", len(html_content))
#         print("Generated time:", generated_time)

#         # Build the full HTML structure in memory
#         full_html = f"""
#         <!DOCTYPE html>
#         <html lang="en">
#         <head>
#             <meta charset="UTF-8">
#             <title>Chat Conversation</title>
#             <style>
#                 body {{ font-family: Arial, sans-serif; margin: 40px; background: white; }}
#                 h1, p {{ text-align: center; }}
#                 .message {{ margin-bottom: 20px; padding: 10px; border-radius: 8px; background-color: #f5f5f5; }}
#                 .message.user {{ background-color: #e1f5fe; }}
#                 .message.assistant {{ background-color: #fff9c4; }}
#                 .message.system {{ background-color: #eeeeee; font-style: italic; }}
#                 pre, code {{ background: #f0f0f0; padding: 5px; border-radius: 5px; overflow-x: auto; }}
#                 img {{ max-width: 100%; }}
#                 a {{ color: #0645AD; }}
#             </style>
#         </head>
#         <body>
#             <h1>Chat Conversation</h1>
#             <p>Generated on {generated_time}</p>
#             {html_content}
#         </body>
#         </html>
#         """

#         # Call Puppeteer via subprocess and pipe stdin/stdout
#         try:
#             process = subprocess.Popen(
#                 ['node', 'generate_pdf_stream.js'],
#                 stdin=subprocess.PIPE,
#                 stdout=subprocess.PIPE,
#                 stderr=subprocess.PIPE
#             )
#             stdout_data, stderr_data = process.communicate(input=full_html.encode('utf-8'))

#             if process.returncode != 0:
#                 logger.error(f"Puppeteer failed: {stderr_data.decode('utf-8')}")
#                 raise HTTPException(status_code=500, detail="Failed to generate PDF")

#             return Response(
#                 content=stdout_data,
#                 media_type="application/pdf",
#                 headers={
#                     "Content-Disposition": "attachment; filename=chat-conversation.pdf"
#                 }
#             )
#         except Exception as e:
#             logger.error(f"Error during Puppeteer PDF generation: {str(e)}")
#             raise HTTPException(status_code=500, detail="Internal server error")
#     except Exception as e:
#         print(f"Error in downloadConversation: {str(e)}")
#         raise
