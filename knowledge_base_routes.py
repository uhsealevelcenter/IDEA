import os
import logging
from pathlib import Path
from typing import List
from fastapi import APIRouter, HTTPException, UploadFile, File  # Depends # TODO: Auth disabled for press release
from fastapi.responses import JSONResponse
import shutil

# from auth import get_auth_token  # TODO: Auth disabled for press release - Import the auth dependency from auth module

logger = logging.getLogger(__name__)

# Create router for knowledge base endpoints
router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])

# Knowledge base directory - works for both local and production
PAPERS_DIR = Path(os.getenv("PAPER_DIRECTORY", "/app/data/papers"))
ALLOWED_PAPER_EXTENSIONS = {'.pdf', '.txt', '.doc', '.docx', '.md'}
MAX_PAPER_SIZE = 50 * 1024 * 1024  # 50MB

def ensure_papers_directory():
    """Ensure the papers directory exists"""
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)

@router.get("/papers")
async def list_papers():  # token: str = Depends(get_auth_token)): # TODO: Auth disabled for press release
    """List all papers in the knowledge base"""
    try:
        ensure_papers_directory()
        
        papers = []
        if PAPERS_DIR.exists():
            for file_path in PAPERS_DIR.iterdir():
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
    
    except Exception as e:
        logger.error(f"Error listing papers: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to list papers")

@router.post("/papers/upload")
async def upload_paper(
    file: UploadFile = File(...)
    # token: str = Depends(get_auth_token) # TODO: Auth disabled for press release
):
    """Upload a new paper to the knowledge base"""
    try:
        ensure_papers_directory()
        
        # Validate file extension
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in ALLOWED_PAPER_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed. Allowed types: {', '.join(ALLOWED_PAPER_EXTENSIONS)}"
            )
        
        # Check if file already exists
        target_path = PAPERS_DIR / file.filename
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
async def delete_paper(filename: str):  # token: str = Depends(get_auth_token)): # TODO: Auth disabled for press release
    """Delete a paper from the knowledge base"""
    try:
        ensure_papers_directory()
        
        # Sanitize filename to prevent path traversal
        if ".." in filename or "/" in filename or "\\" in filename:
            raise HTTPException(status_code=400, detail="Invalid filename")
        
        file_path = PAPERS_DIR / filename
        
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Paper not found")
        
        # Verify the file is in the papers directory
        try:
            file_path.relative_to(PAPERS_DIR)
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Delete the file
        file_path.unlink()
        
        return {"message": f"Paper '{filename}' deleted successfully"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting paper: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete paper")

@router.get("/stats")
async def get_knowledge_base_stats():  # token: str = Depends(get_auth_token)): # TODO: Auth disabled for press release
    """Get statistics about the knowledge base"""
    try:
        ensure_papers_directory()
        
        total_files = 0
        total_size = 0
        file_types = {}
        
        if PAPERS_DIR.exists():
            for file_path in PAPERS_DIR.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in ALLOWED_PAPER_EXTENSIONS:
                    total_files += 1
                    file_size = file_path.stat().st_size
                    total_size += file_size
                    
                    ext = file_path.suffix.lower()
                    if ext in file_types:
                        file_types[ext] += 1
                    else:
                        file_types[ext] = 1
        
        return {
            "total_files": total_files,
            "total_size": total_size,
            "file_types": file_types
        }
    
    except Exception as e:
        logger.error(f"Error getting knowledge base stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get knowledge base statistics") 