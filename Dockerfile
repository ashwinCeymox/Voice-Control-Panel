# ==============================================================================
# Production All-In-One Container for Self-Hosted Local Voice AI Platform
# Runs FastAPI Gateway, Sherpa-ONNX STT, LocalAI Engine, and Web Control Panel
# Compatible with RunPod, Cloud VMs, and Local Docker
# ==============================================================================
FROM ubuntu:22.04

# Avoid interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/root/.local/bin:/usr/local/bin:${PATH}"

WORKDIR /app

# 1. Install system runtime prerequisites
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    bzip2 \
    tar \
    procps \
    python3 \
    python3-pip \
    python3-venv \
    nodejs \
    npm \
    file \
    && rm -rf /var/lib/apt/lists/*

# 2. Pre-install official LocalAI server binary into /usr/local/bin
RUN LOCALAI_NO_SUDO=1 curl -sSL https://localai.io/install.sh | bash && \
    chmod +x /usr/local/bin/local-ai || true

# 3. Copy dependencies and pre-build Python virtual environment
COPY requirements.txt pyproject.toml ./
RUN python3 -m venv /app/.venv && \
    /app/.venv/bin/pip install --upgrade pip setuptools wheel && \
    /app/.venv/bin/pip install -r requirements.txt

# 4. Copy frontend source and pre-compile production assets
COPY frontend/package*.json ./frontend/
RUN cd frontend && npm ci --prefer-offline --no-audit || npm install --no-audit

COPY frontend/ ./frontend/
RUN cd frontend && npm run build

# 5. Pre-download default Sherpa-ONNX streaming STT model
RUN mkdir -p /app/models/streaming-stt && \
    curl -sSL -o /tmp/zipformer.tar.bz2 https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2 && \
    tar -xjf /tmp/zipformer.tar.bz2 -C /app/models/streaming-stt && \
    rm -f /tmp/zipformer.tar.bz2

# 6. Copy remaining project files
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY deploy/ ./deploy/
COPY start.sh ./start.sh

RUN chmod +x ./start.sh ./scripts/lib/*.sh

# Create persistent runtime data directories
RUN mkdir -p /app/.run /app/logs /app/models /app/.install-stamps

# Expose FastAPI Gateway port (public gatekeeper)
EXPOSE 8000

# Health check against FastAPI live endpoint
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health/live || exit 1

# Default launch command: starts LocalAI, Sherpa STT, Gateway, and stays foreground
ENTRYPOINT ["/bin/bash", "-c", "exec ./start.sh up --foreground"]
