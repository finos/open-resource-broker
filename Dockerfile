# Multi-stage Dockerfile for Open Host Factory Plugin REST API
# Optimized for production deployment with security and performance

# Build stage
FROM python:3.11-slim AS builder

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
LABEL org.opencontainers.image.licenses="MIT"

# Install system dependencies for building
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential=12.9 \
    curl=7.88.1-10+deb12u12 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt requirements-dev.txt ./

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install uv for faster dependency installation (optional optimization)
RUN pip install --no-cache-dir uv==0.6.0

# Install Python dependencies with hybrid approach
# Try uv first for speed, fallback to pip if needed
RUN (uv pip install --no-cache --upgrade pip==25.1.1 setuptools==80.9.0 wheel==0.45.1 && \
     uv pip install --no-cache -r requirements.txt) || \
    (pip install --no-cache-dir --upgrade pip==25.1.1 setuptools==80.9.0 wheel==0.45.1 && \
     pip install --no-cache-dir -r requirements.txt)

# Production stage
FROM python:3.11-slim AS production

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
COPY --chown=ohfp:ohfp docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Set entrypoint
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Default command
CMD ["serve"]
