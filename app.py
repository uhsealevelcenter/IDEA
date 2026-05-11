import asyncio
import logging
import os
from pathlib import Path

import litellm
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from starlette.middleware.base import BaseHTTPMiddleware

from backend.guest_manager import cleanup_expired_guest_users
from backend.interpreter_manager import cleanup_idle_sessions
from backend.state import (
    STATIC_DIR,
    limiter,
    GUEST_EXPIRY_CHECK_INTERVAL_SECONDS,
    CLEANUP_INTERVAL,
)
from core.mcp_manager import mcp_manager
from routes.knowledge_base import router as knowledge_base_router, MAX_PAPER_SIZE
from routes.conversations import router as conversation_router
from routes.mcp import router as mcp_router
from routes.hpc import router as hpc_router
from routes.auth import router as auth_router
from routes.users import router as users_router
from routes.prompts import router as prompts_router
from routes.chat import router as chat_router
from routes.files import router as files_router
from core.config import settings
from utils.prompt_manager import init_prompt_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

litellm.request_timeout = 600

root_path = "/idea-api"

app = FastAPI(root_path=root_path)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount("/" + str(STATIC_DIR), StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/assets", StaticFiles(directory="frontend"), name="assets")

init_prompt_manager()

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(prompts_router)
app.include_router(chat_router)
app.include_router(files_router)
app.include_router(knowledge_base_router)
app.include_router(conversation_router, prefix="/conversations", tags=["conversations"])
app.include_router(mcp_router)
app.include_router(hpc_router)

cors_origins_env = os.getenv("CORS_ORIGINS", "")
if cors_origins_env:
    origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
else:
    origins = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://0.0.0.0:8000",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
        "http://0.0.0.0:8001",
        "http://localhost",
        "http://127.0.0.1",
        "http://0.0.0.0",
        "http://172.18.46.161",
        "http://172.18.46.161:8001",
        "https://uhslc.soest.hawaii.edu/research/IDEA",
    ]


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method == "POST":
            path = request.url.path
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    request_size = int(content_length)
                except ValueError:
                    request_size = None

                if request_size is not None:
                    if path.endswith("/knowledge-base/papers/upload"):
                        max_size = MAX_PAPER_SIZE
                    elif path.endswith("/upload"):
                        from backend.state import MAX_FILE_SIZE
                        max_size = MAX_FILE_SIZE
                    else:
                        max_size = None

                    if max_size and request_size > max_size:
                        return JSONResponse(
                            status_code=413,
                            content={"detail": "Request too large"},
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
    retry_after = getattr(exc, "retry_after", None)
    message = (
        f"Too many requests. Please try again in {retry_after} seconds."
        if retry_after
        else "Too many requests. Please try again later."
    )
    return JSONResponse(status_code=429, content={"detail": message})


@app.exception_handler(Exception)
async def http_exception_handler(request, exc):
    from fastapi import HTTPException
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    raise exc


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.on_event("startup")
async def startup():
    async def periodic_cleanup():
        while True:
            try:
                logger.info("Running periodic cleanup of idle sessions")
                await cleanup_idle_sessions()
                await asyncio.sleep(CLEANUP_INTERVAL)
            except Exception as e:
                logger.error(f"Error in periodic cleanup: {str(e)}")
                await asyncio.sleep(60)

    async def periodic_guest_cleanup():
        while True:
            try:
                await cleanup_expired_guest_users()
                await asyncio.sleep(GUEST_EXPIRY_CHECK_INTERVAL_SECONDS)
            except Exception as e:
                logger.error(f"Error in guest cleanup: {str(e)}")
                await asyncio.sleep(60)

    asyncio.create_task(periodic_cleanup())
    asyncio.create_task(periodic_guest_cleanup())


@app.on_event("shutdown")
async def shutdown():
    await mcp_manager.close_all()
