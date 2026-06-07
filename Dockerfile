# ─────────────────────────────────────────────────────────────────────────────
# ClassFlow Watcher — Dockerfile
# Production-ready, multi-stage build with non-root user.
# ─────────────────────────────────────────────────────────────────────────────

# Stage 1: Build / dependency install
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build deps (for psycopg2, google-auth C extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python deps into a separate prefix (easy to copy to stage 2)
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: Runtime image — no build tools, minimal attack surface
FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime OS dep: just libpq (psycopg2 needs it at runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash classflow
USER classflow

# Expose API port
EXPOSE 5001

# Health check — Docker will restart the container if this fails
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5001/health || exit 1

# Default: run the Flask API
CMD ["python", "backend/api.py"]
