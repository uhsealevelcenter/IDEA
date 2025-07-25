import asyncio
import json
from math import ceil
import os
from datetime import date
from time import time
import logging
from typing import Dict
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
# import magic
# import subprocess # For download_conversation (Puppeteer version, under development)

## Required for audio transcription 
#from openai import OpenAI # Uncomment if using OpenAI Whisper API instead of LiteLLM
from litellm import transcription # LiteLLM for audio transcription
from utils.transcription_prompt import transcription_prompt # Transcription prompt for Generic IDEA example (abbreviations, etc.)
#from utils.transcription_prompt_SEA import transcription_prompt # Transcription prompt for SEA-IDEA (abbreviations, etc.)
#from utils.transcription_prompt_CORA import transcription_prompt # Transcription prompt for CORA-IDEA (abbreviations, etc.)

## Specify the system prompt to use (instructions to LLM)
from utils.system_prompt import sys_prompt # Generic IDEA example
#from utils.system_prompt_SEA import sys_prompt # Station Explorer Assistant
#from utils.system_prompt_InSight import sys_prompt # NASA's InSight Mission
#from utils.system_prompt_HCDP import sys_prompt # Hawaii Climate Data Portal (under development)
#from utils.system_prompt_APIcommunicator import sys_prompt # API Communicator (under development)
#from utils.system_prompt_CMEMS import sys_prompt # Copernicus Marine Intelligent Data Exploring Assistant (CM-IDEA)
#from utils.system_prompt_CORA import sys_prompt # NOAA's CORA Intelligent Data Exploring Assistant (CORA-IDEA)

## Specify the custom instructions to use (instructions to LLM and OpenInterpreter)
from utils.custom_instructions import get_custom_instructions # Generic IDEA example
#from utils.custom_instructions_ClimateIndices import get_custom_instructions # Climate Assistant

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IDLE_TIMEOUT = 3600  # 1 hour in seconds
INTERPRETER_PREFIX = "interpreter:"
LAST_ACTIVE_PREFIX = "last_active:"
CLEANUP_INTERVAL = 1800  # Run cleanup every 30 minutes

# Constants for file upload
STATIC_DIR = Path("static")
UPLOAD_DIR = Path("uploads")
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {'.csv', '.txt', '.json', '.nc', '.xlsx', '.mat', '.tif', '.png', '.jpg'} # Added PNG, JPG. & MAT

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
host = (
    "http://localhost"
    if os.getenv("LOCAL_DEV") == "1"
    else "https://uhslc.soest.hawaii.edu/sea"
)

app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount('/' + str(STATIC_DIR), StaticFiles(directory=STATIC_DIR), name="static")

origins = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8001",
    "http://127.0.0.1:8001",
    "http://localhost",
    "*"
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
        if request.method == "POST":
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


async def check_session_upload_limit(session_id: str) -> bool:
    """Check if session has reached upload limit"""
    session_dir = STATIC_DIR / session_id / UPLOAD_DIR
    if not session_dir.exists():
        return True
        
    file_count = sum(1 for _ in session_dir.glob("*") if _.is_file())
    return file_count < MAX_UPLOADS_PER_SESSION

redis_client = redis.Redis(host="redis", port=6379, db=0)
# Global dictionary to store interpreter instances
# Not thread safe, but should be ok for proof of concept
interpreter_instances: Dict[str, OpenInterpreter] = {}

def get_or_create_interpreter(session_id: str) -> OpenInterpreter:
    """Get existing interpreter or create new one"""
    try:
        # Return existing instance if it exists
        if session_id in interpreter_instances:
            logger.info(f"Retrieved existing interpreter for session {session_id}")
            return interpreter_instances[session_id]
        
        # Create new interpreter instance with default settings
        interpreter = OpenInterpreter()
        interpreter.system_message += sys_prompt

        # Enable vision
        interpreter.llm.supports_vision = True 
        
        # OpenAI Models
        interpreter.llm.model = "gpt-4.1-2025-04-14"
        #interpreter.llm.model = "gpt-4o-2024-11-20"
        #interpreter.llm.model = "gpt-4o"
        interpreter.llm.supports_functions = True 

        # # Jetstream2 Models (https://docs.jetstream-cloud.org/inference-service/api/)
        # interpreter.llm.api_key = os.getenv("JETSTREAM2_API_KEY") # api key to send your model 
        # interpreter.llm.api_base = "https://llm.jetstream-cloud.org/api" # add api base for OpenAI compatible provider
        # interpreter.llm.model = "openai/DeepSeek-R1" # add openai/ prefix to route as OpenAI provider
        # interpreter.llm.model = "openai/llama-4-scout" # add openai/ prefix to route as OpenAI provider    
        # interpreter.llm.model = "openai/Llama-3.3-70B-Instruct" # add openai/ prefix to route as OpenAI provider    
        # interpreter.llm.supports_functions = False  # Set to True if your model supports functions (optional)

        # General settings
        interpreter.llm.temperature = 0.2
        # Setting to maximum for gpt-4o as per documentation (https://platform.openai.com/docs/models#gpt-4o)
        interpreter.llm.context_window = 128000
        interpreter.llm.max_tokens = 16383
        interpreter.max_output = 16383
        
        interpreter.llm.max_budget = 0.03
        interpreter.computer.import_computer_api = False
        interpreter.computer.run("python", custom_tool)
        interpreter.auto_run = True

        # Store the instance
        interpreter_instances[session_id] = interpreter
        logger.info(f"Created new interpreter for session {session_id}")
        
        # Store last active time in Redis
        redis_client.set(f"{LAST_ACTIVE_PREFIX}{session_id}", str(time()))
        
        return interpreter

    except Exception as e:
        logger.error(f"Error in get_or_create_interpreter: {str(e)}")
        raise InterpreterError(f"Failed to create/retrieve interpreter: {str(e)}")
    
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

def clear_session(session_id: str):
    """Clear all resources associated with a session"""
    try:
        # Get interpreter instance
        interpreter = interpreter_instances.get(session_id)
        if interpreter:
            # Call reset() to properly terminate all languages and clean up
            interpreter.reset()
            # Remove from instances dict
            del interpreter_instances[session_id]
        
        # Clear Redis keys
        redis_client.delete(f"{LAST_ACTIVE_PREFIX}{session_id}")
        redis_client.delete(f"messages:{session_id}")

        # Remove session directory and all its contents
        session_dir = STATIC_DIR / session_id
        if session_dir.exists():
            # Remove all contents recursively
            import shutil
            shutil.rmtree(session_dir)
        logger.info(f"Cleared session {session_id}")
    except Exception as e:
        logger.error(f"Error clearing session {session_id}: {str(e)}")
        raise

async def cleanup_idle_sessions():
    """Remove interpreter instances and data for idle sessions"""

    try:
        current_time = time()
        logger.info(f"Current time: {current_time}")
        logger.info(f"interpreter_instances: {list(interpreter_instances.keys())}")
        # Check all active sessions
        for session_id in list(interpreter_instances.keys()):
            try:
                last_active = redis_client.get(f"{LAST_ACTIVE_PREFIX}{session_id}")
                if last_active:
                    logger.info(f"Last active time for session {session_id}: {last_active}")
                    
                    last_active_time = float(last_active.decode('utf-8'))
                    if current_time - last_active_time > IDLE_TIMEOUT:
                        clear_session(session_id)
            except Exception as e:
                logger.error(f"Error cleaning up session {session_id}: {str(e)}")
                continue
                
    except Exception as e:
        logger.error(f"Error in cleanup_idle_sessions: {str(e)}")

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
                #model="gpt-4o-transcribe",
                model="gpt-4o-mini-transcribe",
                file=audio_file,
                prompt=transcription_prompt # Optional prompt for transcription guidance (e.g., common abbreviations)
            )

        os.remove(temp_path)
        return {"text": transcription_response.text}

    except Exception as e:
        logger.error(f"Transcription error: {str(e)}")
        raise HTTPException(status_code=500, detail="Transcription failed")


@app.post("/chat")
@limiter.limit(CHAT_RATE_LIMIT)
async def chat_endpoint(request: Request, background_tasks: BackgroundTasks):
    try:
        session_id = request.headers.get("x-session-id")
        if not session_id:
            raise HTTPException(status_code=400, detail="x-session-id header is required")
        
        body = await request.json()
        messages = body.get("messages", [])

        if not messages:
            raise HTTPException(status_code=400, detail="No messages provided")
        
        logger.info(f"Received messages for session {session_id}")
        # Get or create interpreter instance
        interpreter = get_or_create_interpreter(session_id)

        # Add custom instructions (matches SEA design)
        station_id = '000' # Placeholder
        interpreter.custom_instructions =  get_custom_instructions(
            today=today,
            host=host,
            session_id=session_id,
            static_dir=STATIC_DIR,
            upload_dir=UPLOAD_DIR,
            station_id=station_id
        )

        # Update last active time
        redis_client.set(f"{LAST_ACTIVE_PREFIX}{session_id}", str(time()))

        def event_stream():
            try:
                # logger.info(f"Messages sent to interpreter: {messages}") # Debugging
                for result in interpreter.chat(messages[-1], stream=True):
                    data = json.dumps(result) if isinstance(result, dict) else result
                    yield f"data: {data}\n\n"
            except Exception as e:
                logger.error(f"Error in chat stream: {str(e)}")
                error_message = {"error": str(e)}  # <-- fix: use str(e)
                yield f"data: {json.dumps(error_message)}\n\n"
            finally:
                redis_client.set(
                f"messages:{session_id}", json.dumps(interpreter.messages)
            )

        return StreamingResponse(event_stream(), media_type="text/event-stream")
    
    except Exception as e:
        logger.error(f"Unexpected error in chat_endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/history")
def history_endpoint(request: Request):
    session_id = request.headers.get("x-session-id")
    if not session_id:
        return {"error": "x-session-id header is required"}

    stored_messages = redis_client.get(f"messages:{session_id}")
    if stored_messages:
        return json.loads(stored_messages)
    return []


@app.post("/clear")
def clear_endpoint(request: Request):
    try:
        session_id = request.headers.get("x-session-id")
        if not session_id:
            raise HTTPException(status_code=400, detail="x-session-id header is required")

        # redis_client.delete(f"messages:{session_id}")
        clear_session(session_id)
        return {"status": "Chat history cleared"}
    except redis.RedisError as e:
        logger.error(f"Redis error in clear_endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to clear chat history")
    except Exception as e:
        logger.error(f"Unexpected error in clear_endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
    
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
    request: Request = None
):
    try:
        session_id = request.headers.get("x-session-id")
        if not session_id:
            raise HTTPException(status_code=400, detail="Session ID required")
        
        # Check session upload limit
        if not await check_session_upload_limit(session_id):
            raise HTTPException(
                status_code=429,
                detail=f"Upload limit reached. Maximum {MAX_UPLOADS_PER_SESSION} files per session"
            )

        # Create session upload directory if it doesn't exist
        session_dir = STATIC_DIR / session_id / UPLOAD_DIR
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
                            detail=f"File too large. Maximum size: {MAX_FILE_SIZE/1024/1024}MB"
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
                "path": str(final_path.relative_to(STATIC_DIR / session_id / UPLOAD_DIR)),
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
async def delete_file(filename: str, request: Request):
    try:
        session_id = request.headers.get("x-session-id")
        if not session_id:
            raise HTTPException(status_code=400, detail="Session ID required")

        file_path = STATIC_DIR / session_id / UPLOAD_DIR / filename  # Removed "uploads" from path
        
        # Ensure the file exists and is within the session directory
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        
        # Verify the file is in the correct session directory
        try:
            file_path.relative_to(STATIC_DIR / session_id / UPLOAD_DIR)
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
async def list_files(request: Request):
    try:
        session_id = request.headers.get("x-session-id")
        if not session_id:
            raise HTTPException(status_code=400, detail="Session ID required")

        session_dir = STATIC_DIR / session_id / UPLOAD_DIR
        if not session_dir.exists():
            return []

        files = []
        for file_path in session_dir.glob("*"):
            if file_path.is_file():
                files.append({
                    "name": file_path.name,
                    "size": file_path.stat().st_size,
                    "path": str(file_path.relative_to(STATIC_DIR / session_id / UPLOAD_DIR))
                })
        return files

    except Exception as e:
        logger.error(f"List files error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.delete("/files")
async def delete_all_files(request: Request):
    try:
        session_id = request.headers.get("x-session-id")
        if not session_id:
            raise HTTPException(status_code=400, detail="Session ID required")

        session_dir = STATIC_DIR / session_id / UPLOAD_DIR
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