# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

IDEA (Intelligent Data Exploring Assistant) is a web-based AI assistant for geoscientists that provides an interface to OpenAI's GPT models with code execution capabilities via OpenInterpreter. It's a single-user development tool that allows users to interact with data through natural language, execute Python code, and perform data analysis tasks.

### Core Architecture

- **Backend**: FastAPI application (`app.py`) with authentication, file upload, and session management
- **Frontend**: Static HTML/CSS/JavaScript interface served through NGINX
- **Code Execution**: OpenInterpreter integration for running Python code in a controlled environment
- **Authentication**: Simple username/password system with session tokens
- **Data Storage**: Redis for caching, local filesystem for file uploads and data
- **Knowledge Base**: PaperQA2 integration for research paper indexing and retrieval

### Key Components

- `app.py` - Main FastAPI application with all API endpoints
- `auth.py` - Authentication system and session management
- `utils/system_prompt.py` - Core system prompt that defines AI behavior
- `utils/custom_instructions.py` - Domain-specific instructions for the AI
- `utils/prompt_manager.py` - Dynamic prompt management system
- `knowledge_base_routes.py` - PaperQA2 integration for research papers
- `frontend/` - Static web interface (HTML, CSS, JavaScript)
- `data/` - Data directory containing datasets, benchmarks, and research papers

## Development Commands

### Local Development
```bash
# Start local development environment (with hot reload)
./local_start.sh

# Access application at http://localhost
```

### Production Deployment
```bash
# Deploy to production
./production_start.sh
```

### Docker Operations
```bash
# Stop all containers
docker compose down -v

# Build and start containers
docker compose up -d --build

# View logs for specific container
docker logs -f <container_id>
```

### Environment Setup
1. Copy `example.env` to `.env` and configure:
   - `OPENAI_API_KEY` - Required for AI functionality
   - `FIRST_SUPERUSER` and `FIRST_SUPERUSER_PASSWORD` - Authentication credentials
   - `LOCAL_DEV=1` for development, `LOCAL_DEV=0` for production

2. Configure frontend by copying `frontend/config.example.js` to `frontend/config.js`

## Key System Behaviors

### Authentication Flow
- All endpoints except login require valid session tokens
- Sessions expire after 24 hours
- Single-user design - multiple simultaneous users will interfere with each other

### File Upload System
- Supports: `.csv`, `.txt`, `.json`, `.nc`, `.xls`, `.xlsx`, `.doc`, `.docx`, `.ppt`, `.pptx`, `.pdf`, `.md`, `.mat`, `.tif`, `.png`, `.jpg`
- Maximum file size: 10MB
- Files stored in session-specific directories under `static/`
- Rate limited to 5 uploads per minute, 10 files per session

### AI System Prompt Configuration
The AI behavior is controlled by `utils/system_prompt.py` which defines:
- Role as geoscience data exploration assistant
- Code execution capabilities via OpenInterpreter
- Security restrictions (no destructive operations)
- Output formatting preferences (Markdown, MathJax)
- Package scanning requirements using `guarddog`

### PaperQA2 Integration
- Research papers stored in `data/papers/` are automatically indexed
- Settings in `data/.pqa/settings/` control indexing behavior
- Use `pqa` command in conversations to query research literature

## Important Security Notes

- **Single-user limitation**: Not designed for concurrent users
- **Code execution**: Allows arbitrary Python code execution on host system
- **File uploads**: Basic validation only - no comprehensive malware scanning
- **Authentication**: Simple token-based system, not enterprise-grade
- Package installation requires `guarddog` scanning before installation

## Data Architecture

- `data/altimetry/` - Altimetry datasets
- `data/benchmarks/` - Performance benchmarks
- `data/metadata/` - Geographic and metadata files
- `data/papers/` - Research papers for PaperQA2
- `data/prompts/` - Dynamic prompt configurations
- `static/session-*/uploads/` - User-uploaded files per session

## Custom Function Integration

The system supports custom tools via `utils/custom_functions.py` for domain-specific operations beyond standard Python capabilities.

## Development Notes

- Backend uses uvicorn with hot reload in development
- Frontend served via Python's http.server on port 8000 in development
- NGINX handles reverse proxy and static files on port 80
- Redis runs on port 6379 for session caching
- No comprehensive test suite - manual testing required
