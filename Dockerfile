# IDIS Dockerfile - Multi-stage Production Build
# Version: 6.3.0
# Reproducibility: Pin base image digests for deterministic builds

# =============================================================================
# Stage 1: Builder
# =============================================================================
FROM python:3.11-slim-bookworm@sha256:04cd27899595a99dfe77709d96f08876bf2ee99139ee2f0fe9ac948005034e5b AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    ffmpeg \
    libpq-dev \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PIP_CONFIG_FILE=/dev/null

# Copy all source and data files (must precede pip install for hatchling force-include)
COPY pyproject.toml ./
COPY README.md ./
COPY openapi/ ./openapi/
COPY src/ ./src/
COPY prompts/ ./prompts/
COPY schemas/ ./schemas/
COPY requirements/ ./requirements/
COPY scripts/verify_dependency_resolution.py ./scripts/verify_dependency_resolution.py

# Install the package (non-editable so runtime has in-venv code)
RUN python -m pip --isolated install --no-cache-dir --index-url https://pypi.org/simple "pip==26.2.1" && \
    python -m pip --isolated install --no-cache-dir --index-url https://pypi.org/simple \
      -c requirements/constraints-py311.txt \
      --build-constraint requirements/constraints-py311.txt . setuptools wheel && \
    python -m pip check && \
    python scripts/verify_dependency_resolution.py

# Compile Python files for faster startup (optional verification)
RUN python -m compileall -q src/

# =============================================================================
# Stage 2: Governed Poppler prefix
# =============================================================================
FROM python:3.11-slim-bookworm@sha256:04cd27899595a99dfe77709d96f08876bf2ee99139ee2f0fe9ac948005034e5b AS poppler-builder

COPY native/poppler/recipe-v2.json /opt/idis-poppler-build/recipe-v2.json
COPY scripts/build_linux_poppler_prefix.py /opt/idis-poppler-build/build_linux_poppler_prefix.py
ADD --checksum=sha256:c18b40eb36b1a0c5b86e29ca054bf0770304583da4f2cdd42fe86eca6a20de48 https://poppler.freedesktop.org/poppler-25.12.0.tar.xz /opt/idis-poppler-build/poppler-25.12.0.tar.xz

RUN python /opt/idis-poppler-build/build_linux_poppler_prefix.py \
    --prefix /opt/idis-native/poppler-25.12.0 \
    --recipe /opt/idis-poppler-build/recipe-v2.json \
    --source /opt/idis-poppler-build/poppler-25.12.0.tar.xz \
    --manifest-out /opt/idis-native/poppler-25.12.0/share/idis/poppler-linux-prefix-identity-v1.json

# =============================================================================
# Stage 3: Runtime
# =============================================================================
FROM python:3.11-slim-bookworm@sha256:04cd27899595a99dfe77709d96f08876bf2ee99139ee2f0fe9ac948005034e5b AS runtime

# Security: Run as non-root user
RUN groupadd --gid 1000 idis && \
    useradd --uid 1000 --gid idis --shell /bin/bash --create-home idis

WORKDIR /app

# Install runtime dependencies only. libc6, liblzma5, and libpcre2-8-0 stay
# at the base-image versions; the governed prefix replaces distro Poppler.
RUN dpkg-query -W -f '${Package} ${Version}\n' libc6 liblzma5 libpcre2-8-0 > /tmp/idis-base-packages.txt \
    && apt-mark hold libc6 liblzma5 libpcre2-8-0

COPY scripts/build_linux_poppler_prefix.py /tmp/build_linux_poppler_prefix.py
RUN python /tmp/build_linux_poppler_prefix.py --install-runtime-closure \
    && dpkg-query -W -f '${Package} ${Version}\n' \
    libbrotli1 \
    libbsd0 \
    libcairo2 \
    libdeflate0 \
    libfontconfig1 \
    libfreetype6 \
    libjbig0 \
    libjpeg62-turbo \
    liblcms2-2 \
    liblerc4 \
    libopenjp2-7 \
    libpixman-1-0 \
    libpng16-16 \
    libtiff6 \
    libwebp7 \
    libx11-6 \
    libxau6 \
    libxcb-render0 \
    libxcb-shm0 \
    libxcb1 \
    libxdmcp6 \
    libxext6 \
    libxrender1 \
    fontconfig-config \
    fonts-dejavu-core \
    libexpat1 \
    libx11-data \
    > /tmp/idis-closure-packages.txt \
    && apt-mark hold \
    libbrotli1 \
    libbsd0 \
    libcairo2 \
    libdeflate0 \
    libfontconfig1 \
    libfreetype6 \
    libjbig0 \
    libjpeg62-turbo \
    liblcms2-2 \
    liblerc4 \
    libopenjp2-7 \
    libpixman-1-0 \
    libpng16-16 \
    libtiff6 \
    libwebp7 \
    libx11-6 \
    libxau6 \
    libxcb-render0 \
    libxcb-shm0 \
    libxcb1 \
    libxdmcp6 \
    libxext6 \
    libxrender1 \
    fontconfig-config \
    fonts-dejavu-core \
    libexpat1 \
    libx11-data \
    && dpkg-query -W -f '${Package} ${Version}\n' libc6 liblzma5 libpcre2-8-0 > /tmp/idis-after-closure-packages.txt \
    && cmp /tmp/idis-base-packages.txt /tmp/idis-after-closure-packages.txt

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean \
    && dpkg-query -W -f '${Package} ${Version}\n' libc6 liblzma5 libpcre2-8-0 > /tmp/idis-after-apt-packages.txt \
    && cmp /tmp/idis-base-packages.txt /tmp/idis-after-apt-packages.txt \
    && dpkg-query -W -f '${Package} ${Version}\n' \
    libbrotli1 \
    libbsd0 \
    libcairo2 \
    libdeflate0 \
    libfontconfig1 \
    libfreetype6 \
    libjbig0 \
    libjpeg62-turbo \
    liblcms2-2 \
    liblerc4 \
    libopenjp2-7 \
    libpixman-1-0 \
    libpng16-16 \
    libtiff6 \
    libwebp7 \
    libx11-6 \
    libxau6 \
    libxcb-render0 \
    libxcb-shm0 \
    libxcb1 \
    libxdmcp6 \
    libxext6 \
    libxrender1 \
    fontconfig-config \
    fonts-dejavu-core \
    libexpat1 \
    libx11-data \
    > /tmp/idis-after-distro-closure.txt \
    && cmp /tmp/idis-closure-packages.txt /tmp/idis-after-distro-closure.txt \
    && python /tmp/build_linux_poppler_prefix.py --assert-runtime-closure \
    && rm /tmp/build_linux_poppler_prefix.py /tmp/idis-base-packages.txt /tmp/idis-after-apt-packages.txt /tmp/idis-after-closure-packages.txt /tmp/idis-closure-packages.txt /tmp/idis-after-distro-closure.txt

COPY --from=poppler-builder /opt/idis-native/poppler-25.12.0 /opt/idis-native/poppler-25.12.0

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

# Default command: profile-first strict API admission, then the canonical app factory.
CMD ["python", "-m", "idis.runtime.strict_launcher", "api", "--host", "0.0.0.0", "--port", "8000", "--pid-file", "/tmp/idis-api.pid"]
