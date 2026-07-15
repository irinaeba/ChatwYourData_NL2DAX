# ============================================================
# NLtoDAX — FastAPI application container
# ============================================================
# Multi-stage build:
#   Stage 1 (builder): install Python dependencies
#   Stage 2 (runtime): slim image with only what's needed
# ============================================================

# ------------------ Stage 1: Builder -----------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# System packages needed to compile native wheels (cffi, cryptography, etc.)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install all Python dependencies into a virtual-env so we can copy
# just the site-packages to the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ------------------ Stage 2: Runtime -----------------------
FROM python:3.12-slim AS runtime

LABEL maintainer="NLtoDAX Team"
LABEL description="Natural Language to DAX Query Generator"

WORKDIR /app

# Copy the virtual-env from the builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY app.py .
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY cache/ ./cache/
COPY schema_extraction/ ./schema_extraction/

# The .env file is NOT baked in — mount it or pass env vars at runtime
# See docker-compose.yml or `docker run --env-file .env`

# FastAPI / Uvicorn
EXPOSE 8000

# Health check (matches the /health endpoint in app.py)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run with Uvicorn
CMD ["python", "app.py"]
