# IDIS Dockerfile - Multi-stage Production Build
# Version: 6.3.0
# Reproducibility: Pin base image digests for deterministic builds

# =============================================================================
# Stage 1: Builder
# =============================================================================
FROM python:3.11-slim-bookworm@sha256:ce81dc539f0aedc9114cae640f8352fad83d37461c24a3615b01f081d0c0f1c2 AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies first (cache layer)
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Copy application source
COPY src/ ./src/
COPY openapi/ ./openapi/
COPY schemas/ ./schemas/

# Install the package
RUN pip install --no-cache-dir -e .

# Compile Python files for faster startup (optional verification)
RUN python -m compileall -q src/

# =============================================================================
# Stage 2: Runtime
# =============================================================================
FROM python:3.11-slim-bookworm@sha256:ce81dc539f0aedc9114cae640f8352fad83d37461c24a3615b01f081d0c0f1c2 AS runtime

# Security: Run as non-root user
RUN groupadd --gid 1000 idis && \
    useradd --uid 1000 --gid idis --shell /bin/bash --create-home idis

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY --from=builder /build/src/ ./src/
COPY --from=builder /build/openapi/ ./openapi/
COPY --from=builder /build/schemas/ ./schemas/
COPY pyproject.toml ./

# Copy Alembic migrations
COPY src/idis/persistence/migrations/ ./src/idis/persistence/migrations/

# Set ownership
RUN chown -R idis:idis /app

# Switch to non-root user
USER idis

# Environment defaults (fail-closed: require explicit configuration)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    IDIS_VERSION="6.3.0"

# Expose API port
EXPOSE 8000

# Health check - hits /health endpoint
# Fail-closed: container unhealthy if health check fails
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command: run uvicorn with app factory
# Note: Production should override with appropriate workers and settings
CMD ["uvicorn", "idis.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
