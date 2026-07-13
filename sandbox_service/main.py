"""
Sandbox Service - FastAPI microservice owning all persistent
terminal/sandbox state (see terminal_registry.py).

The langgraph service talks to this over HTTP instead of managing
pexpect/microsandbox objects itself, so it can stay stateless with respect
to terminal/sandbox execution.
"""

import hmac
import os
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

import terminal_registry as registry

app = FastAPI(title="Sandbox Service", version="1.0.0")

# Shared secret between this service's only caller (langgraph, via
# langgraph/tools/persistent_terminal.py) and this service itself - not a
# per-user credential. See langgraph/langgraph_service.py for the
# identical pattern. Empty/unset fails OPEN (no check), so dev setups that
# haven't configured it yet keep working - every production .env should
# set this; see example.env. This service has no other authentication of
# its own otherwise: a request that names a sandbox_id can exec/read/
# write/stop/destroy that sandbox, full stop, so this token is the only
# thing standing between "on the docker network" and "full code exec as
# any user" if this service's host ports are ever exposed.
INTERNAL_SERVICE_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "")


def require_internal_token(request: Request) -> None:
    if not INTERNAL_SERVICE_TOKEN:
        return
    auth_header = request.headers.get("authorization", "")
    provided = auth_header[7:] if auth_header.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(provided, INTERNAL_SERVICE_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing internal service token")


class ExecRequest(BaseModel):
    command: str


class ExecResponse(BaseModel):
    success: bool
    output: str
    elapsed_time: float


class WriteFileRequest(BaseModel):
    filepath: str
    content: str
    append: bool = False


class RunPythonRequest(BaseModel):
    code: str


class GrepSearchRequest(BaseModel):
    query: str
    path: str = "."
    regex: bool = True
    case_insensitive: bool = False
    include: Optional[list[str]] = None
    max_results: int = 50


class GlobSearchRequest(BaseModel):
    pattern: str
    path: str = "."
    exclude: Optional[list[str]] = None
    type: str = "any"
    max_results: int = 50


class HealthResponse(BaseModel):
    status: str
    service: str


@app.get("/health", response_model=HealthResponse)
async def health_check():
    return {"status": "healthy", "service": "sandbox"}


@app.post("/sandboxes/{sandbox_id}/exec", response_model=ExecResponse, dependencies=[Depends(require_internal_token)])
async def exec_command(sandbox_id: str, request: ExecRequest):
    success, output, elapsed_time = registry.run_command(request.command, sandbox_id=sandbox_id)
    return {"success": success, "output": output, "elapsed_time": elapsed_time}


@app.post("/sandboxes/{sandbox_id}/files", dependencies=[Depends(require_internal_token)])
async def write_file(sandbox_id: str, request: WriteFileRequest):
    try:
        registry.write_file(
            request.filepath, request.content, sandbox_id=sandbox_id, append=request.append
        )
        return {"ok": True}
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: Cannot write to {request.filepath}")
    except IsADirectoryError:
        raise HTTPException(status_code=400, detail=f"{request.filepath} is a directory, not a file")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write {request.filepath}: {e}")


@app.post("/sandboxes/{sandbox_id}/run-python", dependencies=[Depends(require_internal_token)])
async def run_python(sandbox_id: str, request: RunPythonRequest):
    """
    Execute Python code in sandbox_id's persistent kernel - see
    terminal_registry.run_python / interpreter_kernel/. Always returns
    200 with a {"chunks": [...]} payload (errors are surfaced as a
    console chunk, not an HTTP error), so the langgraph caller doesn't
    need special-case exception handling for this endpoint.
    """
    return registry.run_python(request.code, sandbox_id=sandbox_id)


@app.post("/sandboxes/{sandbox_id}/grep", dependencies=[Depends(require_internal_token)])
async def grep_search(sandbox_id: str, request: GrepSearchRequest):
    """Search file contents in sandbox_id's VM - see terminal_registry.grep_search / interpreter_kernel/."""
    try:
        return registry.grep_search(sandbox_id, **request.model_dump(exclude_none=True))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sandboxes/{sandbox_id}/glob", dependencies=[Depends(require_internal_token)])
async def glob_search(sandbox_id: str, request: GlobSearchRequest):
    """Search files by name in sandbox_id's VM - see terminal_registry.glob_search / interpreter_kernel/."""
    try:
        return registry.glob_search(sandbox_id, **request.model_dump(exclude_none=True))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sandboxes/{sandbox_id}/files/content", dependencies=[Depends(require_internal_token)])
async def read_file_content(sandbox_id: str, filepath: str):
    try:
        data = registry.read_file_bytes(filepath, sandbox_id=sandbox_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {filepath}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read {filepath}: {e}")
    return Response(content=data, media_type="application/octet-stream")


@app.get("/sandboxes/{sandbox_id}/files/exists", dependencies=[Depends(require_internal_token)])
async def check_file_exists(sandbox_id: str, filepath: str):
    return {"exists": registry.file_exists(filepath, sandbox_id=sandbox_id)}


@app.post("/sandboxes/{sandbox_id}/stop", dependencies=[Depends(require_internal_token)])
async def stop_sandbox(sandbox_id: str):
    """Gracefully stop (state-preserving) this sandbox - resumable later."""
    stopped = registry.stop_terminal(sandbox_id)
    return {"ok": True, "stopped": stopped}


@app.post("/sandboxes/{sandbox_id}/destroy", dependencies=[Depends(require_internal_token)])
async def destroy_sandbox(sandbox_id: str):
    """Permanently delete this sandbox - NOT resumable."""
    destroyed = registry.destroy_terminal(sandbox_id)
    return {"ok": True, "destroyed": destroyed}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8020)
