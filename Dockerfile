# Optimized single-stage Dockerfile using pre-built wheel
# Multi-architecture compatible with minimal layers

ARG PYTHON_VERSION=3.13
ARG PACKAGE_NAME_SHORT=ohfp

FROM python:${PYTHON_VERSION}-slim

# Re-declare build arguments for this stage
ARG PACKAGE_NAME_SHORT
ARG BUILD_DATE
ARG VERSION=1.0.0
ARG VCS_REF

# Add metadata labels in single layer
LABEL org.opencontainers.image.title="Open Host Factory Plugin API" \
      org.opencontainers.image.description="REST API for Open Host Factory Plugin - Dynamic cloud resource provisioning" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.vendor="Open Host Factory" \
      org.opencontainers.image.licenses="Apache-2.0"

# Install runtime dependencies and create user in single layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean \
    && groupadd -r "${PACKAGE_NAME_SHORT}" \
    && useradd -r -g "${PACKAGE_NAME_SHORT}" -s /bin/false "${PACKAGE_NAME_SHORT}"

# Set working directory and create directories
WORKDIR /app
RUN mkdir -p /app/logs /app/data /app/tmp

# Install UV and create virtual environment in single layer
RUN pip install --no-cache-dir uv==0.8.11 \
    && uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy pre-built wheel and install dependencies
COPY dist/*.whl /tmp/
RUN uv pip install --no-cache /tmp/*.whl \
    && rm -rf /tmp/*.whl

# Copy only runtime files needed
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY deployment/docker/docker-entrypoint.sh ./docker-entrypoint.sh

# Set permissions and environment in single layer
RUN chmod +x ./docker-entrypoint.sh \
    && chown -R "${PACKAGE_NAME_SHORT}":"${PACKAGE_NAME_SHORT}" /app

# Set all environment variables in single layer
ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    HF_SERVER_ENABLED=true \
    HF_SERVER_HOST=0.0.0.0 \
    HF_SERVER_PORT=8000 \
    HF_SERVER_WORKERS=1 \
    HF_SERVER_LOG_LEVEL=info \
    HF_SERVER_DOCS_ENABLED=true \
    HF_LOGGING_LEVEL=INFO \
    HF_LOGGING_CONSOLE_ENABLED=true \
    HF_STORAGE_STRATEGY=json \
    HF_STORAGE_BASE_PATH=/app/data \
    HF_PROVIDER_TYPE=aws \
    HF_PROVIDER_AWS_REGION=us-east-1

# Auth configuration - these are public configuration settings, not secrets
ENV HF_AUTH_ENABLED=false \
    HF_AUTH_STRATEGY=none

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:${HF_SERVER_PORT}/health', timeout=5).raise_for_status()" || exit 1

# Expose port
EXPOSE 8000

# Switch to non-root user
USER "${PACKAGE_NAME_SHORT}"

# Set entrypoint
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["serve"]
