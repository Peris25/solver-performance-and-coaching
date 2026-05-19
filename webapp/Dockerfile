# =============================================================================
# Stage 1: build deps
# =============================================================================
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System packages required to build psycopg2 and pillow/matplotlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

# Install deps into a self-contained venv we can copy to the runtime stage
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip wheel \
    && pip install -r requirements.txt


# =============================================================================
# Stage 2: runtime
# =============================================================================
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

# Runtime libs only (no compilers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd --create-home --shell /bin/bash app

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv

# Copy app code
WORKDIR /app
COPY --chown=app:app app ./app

# Make sure persistent dirs exist and are writable
RUN mkdir -p /app/data /app/uploads && chown -R app:app /app
USER app

EXPOSE 8000

# Healthcheck — hit /api/health every 30s
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

# Use gunicorn with uvicorn workers in production for proper process management.
# 2 workers is plenty for a single-user portal; tune via env if needed.
CMD ["sh", "-c", "gunicorn app.main:app \
    --workers ${WORKERS:-2} \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:${PORT:-8000} \
    --access-logfile - \
    --error-logfile - \
    --timeout 120"]
