import os
import logging
from pathlib import Path
from typing import List
from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File, Depends
from fastapi.responses import JSONResponse
import shutil

from auth import get_auth_token, get_current_user  # Import auth and user context
from utils.pqa_multi_tenant import (
    get_user_papers_dir,
    get_user_index_dir,
    ensure_user_pqa_settings,
    build_user_index_sync,
    read_index_status,
    write_index_status,
)

logger = logging.getLogger(__name__)

# Create router for knowledge base endpoints
router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])

# Allowed types and limits
ALLOWED_PAPER_EXTENSIONS = {'.pdf', '.txt', '.doc', '.docx', '.md'}
MAX_PAPER_SIZE = 50 * 1024 * 1024  # 50MB

def ensure_papers_directory(papers_dir: Path):
    """Ensure the given user's papers directory exists"""
    papers_dir.mkdir(parents=True, exist_ok=True)


def _has_papers(user_id) -> bool:
    """Return True if the user's papers directory contains at least one allowed file."""
    papers_dir = get_user_papers_dir(user_id)
    if not papers_dir.exists():
        return False
    return any(
        f.is_file() and f.suffix.lower() in ALLOWED_PAPER_EXTENSIONS
        for f in papers_dir.iterdir()
    )


def _clean_user_index(user_id) -> None:
    """Remove all pqa_index_* subdirs when the user has no papers left."""
    index_dir = get_user_index_dir(user_id)
    if not index_dir.exists():
        return
    for child in index_dir.iterdir():
        if child.is_dir() and child.name.startswith("pqa_index"):
            shutil.rmtree(child, ignore_errors=True)
    write_index_status(user_id, status="ready")
    logger.info(f"[PQA] No papers left for user {user_id}, cleaned index directory")


def _background_index_build(user_id) -> None:
    """Background task: build/sync the PQA index for a user after upload or delete.

    If the papers directory is empty (e.g. user deleted all papers) the stale
    index is cleaned up directly instead of calling get_directory_index, which
    avoids a PaperQA bug that can corrupt files.zip during removal of the
    last file.
    """
    import time
    try:
        # Fast path: no papers left → just clean up the index directory
        if not _has_papers(user_id):
            _clean_user_index(user_id)
            return

        t0 = time.perf_counter()
        logger.info(f"[PQA] Background index build starting for user {user_id}")
        build_user_index_sync(user_id)
        logger.info(
            f"[PQA] Background index build complete for user {user_id} "
            f"in {time.perf_counter() - t0:.2f}s"
        )
    except Exception as e:
        logger.error(f"[PQA] Background index build failed for user {user_id}: {e}")

@router.get("/papers")
async def list_papers(token: str = Depends(get_auth_token)):
    """List all papers in the knowledge base for the current user"""
    try:
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        papers_dir = get_user_papers_dir(user.id)
        ensure_papers_directory(papers_dir)
        ensure_user_pqa_settings(user.id)  # ensure user settings exist early

        papers = []
        if papers_dir.exists():
            for file_path in papers_dir.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in ALLOWED_PAPER_EXTENSIONS:
                    papers.append({
                        "name": file_path.name,
                        "size": file_path.stat().st_size,
                        "modified": file_path.stat().st_mtime,
                        "extension": file_path.suffix.lower()
                    })

        # Sort by modification time (newest first)
        papers.sort(key=lambda x: x["modified"], reverse=True)

        return {"papers": papers}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing papers: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to list papers")

@router.post("/papers/upload")
async def upload_paper(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    token: str = Depends(get_auth_token),
):
    """Upload a new paper to the knowledge base for the current user"""
    try:
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        papers_dir = get_user_papers_dir(user.id)
        ensure_papers_directory(papers_dir)
        ensure_user_pqa_settings(user.id)

        # Validate file extension
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in ALLOWED_PAPER_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed. Allowed types: {', '.join(ALLOWED_PAPER_EXTENSIONS)}"
            )

        # Check if file already exists
        target_path = papers_dir / file.filename
        if target_path.exists():
            raise HTTPException(
                status_code=409,
                detail=f"File '{file.filename}' already exists"
            )

        # Save file
        file_size = 0
        with target_path.open("wb") as buffer:
            while chunk := await file.read(8192):
                file_size += len(chunk)
                if file_size > MAX_PAPER_SIZE:
                    buffer.close()
                    target_path.unlink()
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Maximum size: {MAX_PAPER_SIZE/1024/1024}MB"
                    )
                buffer.write(chunk)

        # Trigger background index build so the index is ready before the next query
        background_tasks.add_task(_background_index_build, user.id)

        return {
            "message": "Paper uploaded successfully",
            "filename": file.filename,
            "size": file_size
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading paper: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to upload paper")

@router.delete("/papers/{filename}")
async def delete_paper(
    filename: str,
    background_tasks: BackgroundTasks,
    token: str = Depends(get_auth_token),
):
    """Delete a paper from the knowledge base for the current user"""
    try:
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        papers_dir = get_user_papers_dir(user.id)
        ensure_papers_directory(papers_dir)

        # Sanitize filename to prevent path traversal
        if ".." in filename or "/" in filename or "\\" in filename:
            raise HTTPException(status_code=400, detail="Invalid filename")

        file_path = papers_dir / filename

        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Paper not found")

        # Verify the file is in the papers directory
        try:
            file_path.relative_to(papers_dir)
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied")

        # Delete the file
        file_path.unlink()

        # Re-sync the index in the background (removes deleted file from index)
        background_tasks.add_task(_background_index_build, user.id)

        return {"message": f"Paper '{filename}' deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting paper: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete paper")

@router.get("/stats")
async def get_knowledge_base_stats(token: str = Depends(get_auth_token)):
    """Get statistics about the knowledge base for the current user"""
    try:
        user = get_current_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        papers_dir = get_user_papers_dir(user.id)
        ensure_papers_directory(papers_dir)

        total_files = 0
        total_size = 0
        file_types = {}

        if papers_dir.exists():
            for file_path in papers_dir.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in ALLOWED_PAPER_EXTENSIONS:
                    total_files += 1
                    file_size = file_path.stat().st_size
                    total_size += file_size

                    ext = file_path.suffix.lower()
                    if ext in file_types:
                        file_types[ext] += 1
                    else:
                        file_types[ext] = 1

        # Include index build status for observability
        index_status = read_index_status(user.id)

        return {
            "total_files": total_files,
            "total_size": total_size,
            "file_types": file_types,
            "index_status": index_status,
        }

    except Exception as e:
        logger.error(f"Error getting knowledge base stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get knowledge base statistics") 