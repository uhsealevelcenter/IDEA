"""
Sandbox Service - FastAPI microservice owning all persistent
terminal/sandbox state (see terminal_registry.py).

The langgraph service talks to this over HTTP instead of managing
pexpect/microsandbox objects itself, so it can stay stateless with respect
to terminal/sandbox execution.
"""

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import terminal_registry as registry

app = FastAPI(title="Sandbox Service", version="1.0.0")


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


class HealthResponse(BaseModel):
    status: str
    service: str


@app.get("/health", response_model=HealthResponse)
async def health_check():
    return {"status": "healthy", "service": "sandbox"}


@app.post("/sandboxes/{sandbox_id}/exec", response_model=ExecResponse)
async def exec_command(sandbox_id: str, request: ExecRequest):
    success, output, elapsed_time = registry.run_command(request.command, sandbox_id=sandbox_id)
    return {"success": success, "output": output, "elapsed_time": elapsed_time}


@app.post("/sandboxes/{sandbox_id}/files")
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


@app.get("/sandboxes/{sandbox_id}/files/content")
async def read_file_content(sandbox_id: str, filepath: str):
    try:
        data = registry.read_file_bytes(filepath, sandbox_id=sandbox_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {filepath}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read {filepath}: {e}")
    return Response(content=data, media_type="application/octet-stream")


@app.get("/sandboxes/{sandbox_id}/files/exists")
async def check_file_exists(sandbox_id: str, filepath: str):
    return {"exists": registry.file_exists(filepath, sandbox_id=sandbox_id)}


@app.post("/sandboxes/{sandbox_id}/stop")
async def stop_sandbox(sandbox_id: str):
    """Gracefully stop (state-preserving) this sandbox - resumable later."""
    stopped = registry.stop_terminal(sandbox_id)
    return {"ok": True, "stopped": stopped}


@app.post("/sandboxes/{sandbox_id}/destroy")
async def destroy_sandbox(sandbox_id: str):
    """Permanently delete this sandbox - NOT resumable."""
    destroyed = registry.destroy_terminal(sandbox_id)
    return {"ok": True, "destroyed": destroyed}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8020)
