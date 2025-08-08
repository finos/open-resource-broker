# Multi-stage Dockerfile for Open Host Factory Plugin REST API
# Optimized for production deployment with UV-first architecture

# Build arguments for Python version
ARG PYTHON_VERSION=3.11

# Build stage
FROM python:${PYTHON_VERSION}-slim AS builder

# Set build arguments
ARG BUILD_DATE
ARG VERSION=1.0.0
ARG VCS_REF

# Add metadata labels
LABEL org.opencontainers.image.title="Open Host Factory Plugin API"
LABEL org.opencontainers.image.description="REST API for Open Host Factory Plugin - Dynamic cloud resource provisioning"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL org.opencontainers.image.revision="${VCS_REF}"
LABEL org.opencontainers.image.vendor="Open Host Factory"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# Install system dependencies for building
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install UV for fast dependency management
RUN pip install --no-cache-dir uv

# Copy pyproject.toml and other config files first for better caching
COPY pyproject.toml ./
COPY src/_version.py src/_version.py
COPY src/_package.py src/_package.py

# Create virtual environment and install dependencies
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install the package and its dependencies
RUN uv pip install --no-cache .

# Production stage
FROM python:${PYTHON_VERSION}-slim AS production

# Install runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
RUN groupadd -r ohfp && useradd -r -g ohfp -s /bin/false ohfp

# Set working directory
WORKDIR /app

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY src/ ./src/
COPY config/ ./config/
COPY scripts/ ./scripts/

# Copy configuration files
COPY pyproject.toml ./

# Create necessary directories and set permissions
RUN mkdir -p /app/logs /app/data /app/tmp && \
    chown -R ohfp:ohfp /app

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

# Default configuration environment variables
ENV HF_SERVER_ENABLED=true
ENV HF_SERVER_HOST=0.0.0.0
ENV HF_SERVER_PORT=8000
ENV HF_SERVER_WORKERS=1
ENV HF_SERVER_LOG_LEVEL=info
ENV HF_SERVER_DOCS_ENABLED=true

# Authentication configuration (non-sensitive defaults)
ENV HF_AUTH_ENABLED=false
ENV HF_AUTH_STRATEGY=none

# Logging configuration
ENV HF_LOGGING_LEVEL=INFO
ENV HF_LOGGING_CONSOLE_ENABLED=true

# Storage configuration
ENV HF_STORAGE_STRATEGY=json
ENV HF_STORAGE_BASE_PATH=/app/data

# Provider configuration
ENV HF_PROVIDER_TYPE=aws
ENV HF_PROVIDER_AWS_REGION=us-east-1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${HF_SERVER_PORT}/health || exit 1

# Expose port
EXPOSE 8000

# Switch to non-root user
USER ohfp

# Create entrypoint script
COPY --chown=ohfp:ohfp deployment/docker/docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Set entrypoint
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Default command
CMD ["serve"]
FROM python:${PYTHON_VERSION}-slim AS production

# Install runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl=7.88.1-10+deb12u12 \
    ca-certificates=20230311+deb12u1 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
RUN groupadd -r ohfp && useradd -r -g ohfp -s /bin/false ohfp

# Set working directory
WORKDIR /app

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY src/ ./src/
COPY config/ ./config/
COPY scripts/ ./scripts/

# Copy configuration files
COPY pyproject.toml setup.py ./

# Create necessary directories and set permissions
RUN mkdir -p /app/logs /app/data /app/tmp && \
    chown -R ohfp:ohfp /app

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

# Default configuration environment variables
ENV HF_SERVER_ENABLED=true
ENV HF_SERVER_HOST=0.0.0.0
ENV HF_SERVER_PORT=8000
ENV HF_SERVER_WORKERS=1
ENV HF_SERVER_LOG_LEVEL=info
ENV HF_SERVER_DOCS_ENABLED=true

# Authentication configuration (non-sensitive defaults)
ENV HF_AUTH_ENABLED=false
ENV HF_AUTH_STRATEGY=none

# Logging configuration
ENV HF_LOGGING_LEVEL=INFO
ENV HF_LOGGING_CONSOLE_ENABLED=true

# Storage configuration
ENV HF_STORAGE_STRATEGY=json
ENV HF_STORAGE_BASE_PATH=/app/data

# Provider configuration
ENV HF_PROVIDER_TYPE=aws
ENV HF_PROVIDER_AWS_REGION=us-east-1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${HF_SERVER_PORT}/health || exit 1

# Expose port
EXPOSE 8000

# Switch to non-root user
USER ohfp

# Create entrypoint script
COPY --chown=ohfp:ohfp deployment/docker/docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Set entrypoint
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Default command
CMD ["serve"]
