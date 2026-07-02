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
COPY multi_agent.py .
COPY langgraph_service.py .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV OPENAI_API_KEY=""

# Expose port for the API service
EXPOSE 8010

# Run the LangGraph service
CMD ["uvicorn", "langgraph_service:app", "--host", "0.0.0.0", "--port", "8010"]
