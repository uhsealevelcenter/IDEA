# LangGraph Terminal Agent Microservice

A production-ready AI agent microservice built on LangGraph and LangChain, providing a persistent terminal session for executing commands, writing scripts, and solving tasks iteratively. Fully integrated with IDEA's `/chat-runs` async job queue architecture.

## Architecture Overview

### **Microservice Deployment**
- **Independent FastAPI Service**: Runs on port 8010 in separate Docker container
- **Async Job Queue**: `/chat-runs` endpoints for non-blocking execution
- **Redis-Backed**: Job status and event streaming via Redis
- **Main App Proxy**: `/chat-runs-langgraph` endpoints in main app forward to microservice
- **Frontend Integration**: Full SSE event streaming with `start`/`end` flags

### **Key Components**
- **`langgraph_service.py`**: FastAPI server with `/chat-runs` endpoints
- **`multi_agent.py`**: ConversationOrchestrator with streaming support
- **`agents/terminal_agent.py`**: TerminalAgent with LangChain tool binding
- **`tools/persistent_terminal.py`**: Reliable 30-min timeout terminal sessions

## Features

### **Core Capabilities**
- **Persistent Terminal Session**: Execute bash commands with persistent state (environment variables, working directory, etc.)
- **Natural Language Interface**: Describe tasks in plain English
- **Iterative Problem Solving**: Agent can debug and iterate until tasks are complete
- **Extended Timeout**: 30-minute timeout for long-running operations
- **Full Logging**: Complete logs and messages without truncation

### **Real-Time Streaming**
- **Shell Commands**: Streamed as syntax-highlighted code blocks (`type: 'code', format: 'shell'`)
- **Command Output**: Streamed to console panels (`type: 'console', format: 'output'`)
- **File Operations**: File writes displayed with language-specific syntax highlighting
- **Status Updates**: Real-time tool execution feedback
- **Images**: Displayed explicitly via `show_image_tool`, base64 encoding, and inline display

### **Explicit Image Display**
- The LLM calls `show_image_tool(filepath)` whenever it wants to show an image
- Supports `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.webp`, `.svg` files
- Base64-encodes the file on demand
- Streams as `type: 'image', format: 'base64.{ext}'` chunks
- Displays inline in chat (Open Interpreter style)

### **Async Job Queue**
- Non-blocking execution via `/chat-runs` endpoints
- Redis-backed status and event storage
- Background thread processing
- Polling-based event streaming
- Compatible with existing IDEA frontend

## Deployment

### **Docker Compose (Recommended)**

1. Ensure environment variables are set (Azure AI Foundry OpenAI-compatible endpoint):
```bash
export OPENAI_API_KEY="your-azure-api-key-here"
export OPENAI_BASE_URL="https://<your-resource>.services.ai.azure.com/openai/v1"
```

2. Start the entire stack:
```bash
docker compose up -d
```

The LangGraph service will be available at `http://localhost:8010` and proxied through the main app at `/chat-runs-langgraph`.

### **Services**
- **Main App** (`idea_container`): Port 8001, proxies requests to LangGraph
- **LangGraph** (`idea_langgraph`): Port 8010, runs terminal agent
- **Redis**: Stores job status and events
- **Nginx**: Routes frontend requests

### **Volume Mounts (Development)**
```yaml
volumes:
  - ./langgraph:/app  # Hot reload for development
```
Files created by the agent appear in `./langgraph/` on your local filesystem.

## API Endpoints

### **Main App Proxy Endpoints**

#### Start Chat Run
```http
POST /chat-runs-langgraph
Authorization: Bearer <token>
Content-Type: application/json

{
  "message": "Create a visualization of sea level rise in Hawaii",
  "session_id": "session-abc123"
}

Response:
{
  "run_id": "50317ad2a1864fd583f0a7d244f0c7f2",
  "status": "running"
}
```

#### Get Run Status
```http
GET /chat-runs-langgraph/{run_id}
Authorization: Bearer <token>

Response:
{
  "run_id": "50317ad2...",
  "status": "completed",  # or "running", "failed"
  "error": null
}
```

#### Poll Events
```http
GET /chat-runs-langgraph/{run_id}/events?after=0
Authorization: Bearer <token>

Response:
{
  "run_id": "50317ad2...",
  "status": "running",
  "events": [
    {
      "seq": 1,
      "chunk": {
        "role": "assistant",
        "type": "message",
        "content": "I'll create a visualization...",
        "start": true
      }
    },
    {
      "seq": 2,
      "chunk": {
        "role": "computer",
        "type": "code",
        "format": "python",
        "content": "import matplotlib.pyplot as plt\n...",
        "start": true,
        "end": true
      }
    },
    {
      "seq": 3,
      "chunk": {
        "role": "computer",
        "type": "image",
        "format": "base64.png",
        "content": "iVBORw0KGgo...",
        "start": true,
        "end": true
      }
    }
  ],
  "next_after": 3,
  "error": null
}
```

### **Direct Microservice Endpoints** (Port 8010)

Same schema as above, but without `/langgraph` prefix:
- `POST /chat-runs`
- `GET /chat-runs/{run_id}`
- `GET /chat-runs/{run_id}/events`

## Event Streaming Format

### **Message Types**

The agent streams different message types to the frontend:

#### **1. Text Messages**
```json
{
  "role": "assistant",
  "type": "message",
  "content": "I'll help you create that visualization...",
  "start": true,
  "end": false
}
```

#### **2. Shell Commands**
```json
{
  "role": "computer",
  "type": "code",
  "format": "shell",
  "content": "python hawaii_sea_level.py",
  "start": true,
  "end": true
}
```

#### **3. File Writes**
```json
{
  "role": "computer",
  "type": "code",
  "format": "python",
  "content": "import matplotlib.pyplot as plt\n...",
  "start": true,
  "end": true
}
```

#### **4. Console Output**
```json
{
  "role": "computer",
  "type": "console",
  "format": "output",
  "content": "✓ Wrote 1234 characters (45 lines) to script.py",
  "start": true,
  "end": true
}
```

#### **5. Images** (via `show_image_tool`)
```json
{
  "role": "assistant",
  "type": "image",
  "format": "base64.png",
  "content": "iVBORw0KGgoAAAANSUhEUgAAA...",
  "start": true,
  "end": true
}
```

### **Flags**
- `start: true` - First chunk of a message (creates new message bubble)
- `end: true` - Last chunk of a message (marks as complete, enables features like math typesetting)
- Both flags on same chunk = single-chunk complete message

## Terminal Capabilities

The persistent terminal session allows:
- Installing packages (pip, apt, etc.)
- Managing files and directories
- Running scripts in any language
- Setting environment variables
- Changing working directories
- Long-running operations (up to 30 minutes)

## Configuration

### Timeout
Default timeout: 1800 seconds (30 minutes)
Can be modified in `PersistentTerminal.__init__()`:
```python
PersistentTerminal(timeout=1800)  # 30 minutes
```

### Max Iterations
Default: 20 iterations
Can be modified when creating the agent:
```python
agent = TerminalAgent(max_iterations=30)
```

## Agent Tools

### **1. run_terminal_tool**
Executes shell commands in persistent terminal session.

```python
run_terminal_tool(command="pip install matplotlib")
# Returns: "✓ Command executed successfully in 3s.\nOutput:\n..."
```

**Features:**
- Persistent environment (cd, export, etc. persist)
- 30-minute timeout for long-running commands
- Real-time progress monitoring
- Proper error handling and exit codes

### **2. write_file_tool**
Writes content to files (use this instead of shell redirection).

```python
write_file_tool(
    filepath="script.py",
    content="print('Hello World')\n",
    append=False
)
# Returns: "✓ Wrote 19 characters (1 lines) to script.py"
```

**Features:**
- Creates parent directories automatically
- Handles encoding properly
- Supports append mode
- Returns file stats

## Configuration

### **Environment Variables**

```yaml
# Required
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://<your-resource>.services.ai.azure.com/openai/v1

# Optional
REDIS_HOST=redis         # Default: redis
REDIS_PORT=6379          # Default: 6379
PYTHONUNBUFFERED=1      # Recommended for logging
```

### **Service Configuration**

#### LangGraph Service
```yaml
# docker-compose.override.yml
langgraph:
  volumes:
    - ./langgraph:/app
  command: uvicorn langgraph_service:app --host 0.0.0.0 --port 8010
  # Note: --reload is DISABLED to prevent interruption during file creation
```

#### Main App
```yaml
web:
  command: uvicorn app:app --reload --reload-exclude 'langgraph/*' --host 0.0.0.0 --port 8001
  # Excludes langgraph/* to prevent auth session invalidation
```

### **Agent Parameters**

```python
agent = TerminalAgent(
    model="gpt-4o-mini",     # Azure AI Foundry deployment name
    temperature=0.2,         # 0.0-1.0, lower = more deterministic
    max_iterations=20        # Max iteration limit
)
```

## Example Use Cases

### **Data Visualization**
```
User: "Find NOAA sea level data for Hawaii and create a visualization"

Agent:
1. Queries NOAA API for tide gauge data
2. Creates Python script with matplotlib
3. Runs script to generate PNG
4. Calls `show_image_tool(filepath)` to display the image inline
```

### **Code Execution**
```
User: "Write and run code for Fibonacci sequence n=50"

Agent:
1. Writes Python script
2. Executes with python command
3. Streams output to console
```

### **Package Installation & Testing**
```
User: "Install pandas and create sample data analysis"

Agent:
1. Runs pip install pandas
2. Creates analysis script
3. Executes and shows results
```

### **Multi-Step Tasks**
```
User: "Set up a web scraper for weather data"

Agent:
1. Installs beautifulsoup4 and requests
2. Writes scraper script
3. Tests on sample URL
4. Debugs and iterates until working
5. Saves final version
```

## Implementation Details

### **Redis Storage Schema**

#### Run Status
```python
# Key: langgraph_run:{run_id}
{
    "status": "running",  # or "completed", "failed"
    "error": null,
    "user_id": "3d6bfbea-71b6-4ebe-a59e-c846e39d0134"
}
# TTL: 3600 seconds
```

#### Events List
```python
# Key: langgraph_run_events:{run_id}
[
    {"seq": 1, "chunk": {...}},
    {"seq": 2, "chunk": {...}},
    ...
]
# TTL: 3600 seconds
```

#### Sequence Counter
```python
# Key: langgraph_run_seq:{run_id}
42  # Current sequence number
# TTL: 3600 seconds
```

### **Image Display**

When the LLM calls `show_image_tool(filepath)`, the agent:
1. Validates the file exists and has a supported image extension
2. Base64-encodes the image
3. Streams as two chunks:
   - Chunk 1: Empty content with `start: true`
   - Chunk 2: Base64 data with `end: true`
5. Frontend renders on `isComplete = true`

**Supported formats:** `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.webp`, `.svg`

### **Tool Call Streaming**

When LLM calls a tool:
1. **Command Display**: Stream as `type: 'code', format: 'shell'`
2. **Execution**: Run in persistent terminal
3. **Output Display**: Stream as `type: 'console', format: 'output'`
4. **Image Display**: LLM calls `show_image_tool(filepath)` to stream an image when it wants to show one

This creates the Open Interpreter experience where users see:
- What code is being written
- What commands are running
- What output is produced
- What images are created

## Troubleshooting

### **401 Unauthorized Errors**

**Symptom:** Getting 401 errors immediately after login

**Cause:** Auth sessions are stored in-memory and cleared on container restart. Creating files triggers auto-reload in development.

**Solution:**
1. Set `--reload-exclude 'langgraph/*'` in main app (already configured)
2. Disable `--reload` on LangGraph service (already configured)
3. Re-login if container restarts

### **Images Not Displaying**

**Symptom:** Seeing broken image icon

**Cause:** Base64 data was duplicated due to `start` and `end` both being true

**Solution:** Images are split into two chunks (empty `start` chunk, then data on the `end` chunk)

### **Job Stuck in "Running" State**

**Symptom:** Frontend shows "Step is still running" with no events

**Cause:** Container restarted mid-execution, killing background thread

**Solution:**
1. Check logs: `docker logs idea_langgraph --tail 100`
2. Verify `--reload` is disabled
3. Ensure volume mounts aren't triggering restarts

### **Files Not Persisting**

**Symptom:** Agent-created files disappear after container restart

**Solution:** Volume mounts are configured - files appear in `./langgraph/` locally. They persist across restarts.

## Production Considerations

### **Security**
- Agent has full terminal access - run in isolated container
- Limit network access if executing untrusted code
- Consider sandboxing for multi-tenant deployments

### **Performance**
- Redis for event storage - scales horizontally
- Background threads prevent blocking
- Consider task queue (Celery) for high volume

### **Storage**
- Agent workspace is volume-mounted in development
- For production, use dedicated volume or temp directory
- Add cleanup job for old images/scripts

### **Monitoring**
- Token usage tracked per iteration
- Cost estimates calculated (GPT-4o pricing)
- Full conversation logs available
- Redis TTL prevents memory leaks (1 hour default)

## Notes

- Terminal session persists across command executions
- Full logs retained without truncation
- Real-time streaming with proper `start`/`end` flags
- 30-minute timeout for long-running tasks
- Explicit, LLM-triggered image display via `show_image_tool`
- Compatible with Open Interpreter frontend patterns
- Production-ready async job queue architecture
