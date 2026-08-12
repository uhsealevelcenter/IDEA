# Dockerfile for LangGraph Terminal Agent
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    bash \
    git \
    curl \
    wget \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# NOTE: this service no longer talks to microsandbox/pexpect directly - all
# terminal/sandbox execution is delegated over HTTP to the sandbox_service
# (see tools/persistent_terminal.py, /sandbox_service/). No msb install or
# /dev/kvm passthrough needed here anymore.

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install FastAPI and related dependencies for the service
RUN pip install --no-cache-dir fastapi uvicorn[standard] httpx redis

# Copy all necessary code
COPY agents/ ./agents/
COPY tools/ ./tools/
COPY utils/ ./utils/
COPY idea_graph/ ./idea_graph/
COPY multi_agent.py .
COPY langgraph_service.py .
COPY idea_config.py .
COPY progress.py .

# Fail the image build if a root module is omitted or the service import graph
# is otherwise incomplete. This runs without contacting Redis/PostgreSQL.
RUN python -c "import progress; import langgraph_service"

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Expose port for the API service
EXPOSE 8010

# Run the LangGraph service
CMD ["uvicorn", "langgraph_service:app", "--host", "0.0.0.0", "--port", "8010"]
