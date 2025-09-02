# Docker Deployment Guide

## Overview

The Open Host Factory Plugin REST API provides comprehensive Docker support for containerized deployment with:

- **Multi-stage Dockerfile** for optimized production images
- **Docker Compose** configurations for development and production
- **Environment-based configuration** with full override support
- **Security hardening** with non-root user and minimal attack surface
- **Health checks** and monitoring integration
- **Multi-architecture support** (AMD64, ARM64)

## Quick Start

### 1. Development Setup

```bash
# Clone the repository
git clone <repository-url>
cd open-hostfactory-plugin

# Copy environment template
cp .env.example .env

# Edit configuration
vim .env

# Start development environment
docker-compose up -d

# View logs
docker-compose logs -f ohfp-api

# Access API documentation
open http://localhost:8000/docs
```

### 2. Production Deployment

```bash
# Build production image
./docker/build.sh --version 1.0.0 --registry your-registry.com

# Deploy to production
docker-compose -f docker-compose.prod.yml up -d

# Check status
docker-compose -f docker-compose.prod.yml ps
```

## Configuration

### Environment Variables

The container supports comprehensive configuration via environment variables:

#### Server Configuration
```bash
HF_SERVER_ENABLED=true          # Enable REST API server
HF_SERVER_HOST=0.0.0.0          # Server bind address
HF_SERVER_PORT=8000             # Server port
HF_SERVER_WORKERS=4             # Number of worker processes
HF_SERVER_LOG_LEVEL=info        # Server log level
HF_SERVER_DOCS_ENABLED=false    # Enable API documentation
```

#### Authentication Configuration
```bash
HF_AUTH_ENABLED=true                    # Enable authentication
HF_AUTH_STRATEGY=bearer_token           # Auth strategy (none, bearer_token, iam, cognito)
HF_AUTH_BEARER_SECRET_KEY=your-secret   # JWT secret key
HF_AUTH_BEARER_TOKEN_EXPIRY=3600        # Token expiry in seconds
```

#### AWS Configuration
```bash
HF_PROVIDER_TYPE=aws                    # Provider type
HF_PROVIDER_AWS_REGION=us-east-1        # AWS region
AWS_ACCESS_KEY_ID=your-key-id           # AWS credentials (use IAM roles in production)
AWS_SECRET_ACCESS_KEY=your-secret-key   # AWS secret key
```

#### Storage Configuration
```bash
HF_STORAGE_STRATEGY=json                # Storage strategy
HF_STORAGE_BASE_PATH=/app/data          # Data storage path
```

### Configuration Files

Configuration files are loaded in the following order (highest precedence first):

1. **Environment variables** (highest precedence)
2. `/app/config/docker.json` (Docker-specific config)
3. `/app/config/production.json` (Production config)
4. `/app/config/default_config.json` (Default fallback)

#### Example Docker Configuration

```json
{
  "server": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 8000,
    "workers": 4,
    "auth": {
      "enabled": true,
      "strategy": "bearer_token",
      "bearer_token": {
        "secret_key": "your-production-secret-key",
        "token_expiry": 3600
      }
    }
  },
  "provider": {
    "type": "aws",
    "aws": {
      "region": "us-east-1"
    }
  }
}
```

## Docker Commands

### Building Images

```bash
# Build development image
docker build -t ohfp-api:dev .

# Build production image with build script
./docker/build.sh --version 1.0.0

# Build multi-architecture image
./docker/build.sh --platforms linux/amd64,linux/arm64 --push --registry your-registry.com
```

### Running Containers

```bash
# Run development container
docker run -d \
  --name ohfp-api \
  -p 8000:8000 \
  -e HF_SERVER_ENABLED=true \
  -e HF_AUTH_ENABLED=false \
  -v $(pwd)/config:/app/config:ro \
  -v $(pwd)/data:/app/data \
  ohfp-api:latest

# Run with authentication
docker run -d \
  --name ohfp-api-auth \
  -p 8000:8000 \
  -e HF_SERVER_ENABLED=true \
  -e HF_AUTH_ENABLED=true \
  -e HF_AUTH_STRATEGY=bearer_token \
  -e HF_AUTH_BEARER_SECRET_KEY=your-secret-key \
  ohfp-api:latest

# Run with AWS credentials
docker run -d \
  --name ohfp-api-aws \
  -p 8000:8000 \
  -e HF_SERVER_ENABLED=true \
  -e AWS_ACCESS_KEY_ID=your-key \
  -e AWS_SECRET_ACCESS_KEY=your-secret \
  -e HF_PROVIDER_AWS_REGION=us-east-1 \
  ohfp-api:latest
```

### Container Management

```bash
# View logs
docker logs -f ohfp-api

# Execute commands in container
docker exec -it ohfp-api bash

# Run CLI commands
docker exec ohfp-api python src/run.py templates list

# Health check
docker exec ohfp-api curl -f http://localhost:8000/health

# Stop container
docker stop ohfp-api

# Remove container
docker rm ohfp-api
```

## Docker Compose

### Development Environment

```bash
# Start all services
docker-compose up -d

# Start specific services
docker-compose up -d ohfp-api

# View logs
docker-compose logs -f

# Scale services
docker-compose up -d --scale ohfp-api=3

# Stop services
docker-compose down

# Remove volumes
docker-compose down -v
```

### Production Environment

```bash
# Deploy production stack
docker-compose -f docker-compose.prod.yml up -d

# Update production deployment
docker-compose -f docker-compose.prod.yml pull
docker-compose -f docker-compose.prod.yml up -d --no-deps ohfp-api

# View production logs
docker-compose -f docker-compose.prod.yml logs -f

# Production health check
docker-compose -f docker-compose.prod.yml exec ohfp-api curl -f http://localhost:8000/health
```

## Security

### Container Security

- **Non-root user**: Container runs as `ohfp` user (UID/GID 1000)
- **Read-only filesystem**: Production containers use read-only root filesystem
- **No new privileges**: Security option prevents privilege escalation
- **Minimal attack surface**: Multi-stage build with minimal runtime dependencies
- **Security scanning**: Regular vulnerability scanning recommended

### Network Security

```bash
# Create custom network
docker network create --driver bridge ohfp-network

# Run with custom network
docker run -d \
  --name ohfp-api \
  --network ohfp-network \
  -p 8000:8000 \
  ohfp-api:latest
```

### Secrets Management

```bash
# Use Docker secrets (Docker Swarm)
echo "your-secret-key" | docker secret create ohfp-jwt-secret -

# Use environment file
docker run -d \
  --name ohfp-api \
  --env-file .env.production \
  ohfp-api:latest

# Use external secrets management
docker run -d \
  --name ohfp-api \
  -e HF_AUTH_BEARER_SECRET_KEY_FILE=/run/secrets/jwt-secret \
  -v /path/to/secrets:/run/secrets:ro \
  ohfp-api:latest
```

## Monitoring

### Health Checks

```bash
# Container health check
docker inspect --format='{{.State.Health.Status}}' ohfp-api

# Manual health check
curl -f http://localhost:8000/health

# Health check with authentication
curl -f -H "Authorization: Bearer your-token" http://localhost:8000/health
```

### Logging

```bash
# View container logs
docker logs ohfp-api

# Follow logs
docker logs -f ohfp-api

# View logs with timestamps
docker logs -t ohfp-api

# View last N lines
docker logs --tail 100 ohfp-api
```

### Metrics

```bash
# Container resource usage
docker stats ohfp-api

# Container processes
docker exec ohfp-api ps aux

# Container filesystem usage
docker exec ohfp-api df -h
```

## Troubleshooting

### Common Issues

#### Container Won't Start
```bash
# Check container logs
docker logs ohfp-api

# Check container configuration
docker inspect ohfp-api

# Run container interactively
docker run -it --rm ohfp-api:latest bash
```

#### Authentication Issues
```bash
# Check authentication configuration
docker exec ohfp-api python -c "
from src.config.manager import ConfigurationManager
config = ConfigurationManager()
print(config.get_typed(ServerConfig).auth.enabled)
"

# Test authentication endpoint
curl -v http://localhost:8000/info
```

#### AWS Connectivity Issues
```bash
# Check AWS credentials
docker exec ohfp-api aws sts get-caller-identity

# Check AWS region
docker exec ohfp-api env | grep AWS

# Test AWS connectivity
docker exec ohfp-api python -c "
import boto3
ec2 = boto3.client('ec2')
print(ec2.describe_regions())
"
```

#### Performance Issues
```bash
# Check resource usage
docker stats ohfp-api

# Check container limits
docker inspect ohfp-api | grep -A 10 Resources

# Increase worker processes
docker run -d \
  --name ohfp-api \
  -e HF_SERVER_WORKERS=4 \
  ohfp-api:latest
```

### Debug Mode

```bash
# Run in debug mode
docker run -d \
  --name ohfp-api-debug \
  -p 8000:8000 \
  -e HF_DEBUG=true \
  -e HF_LOGGING_LEVEL=DEBUG \
  ohfp-api:latest

# Interactive debugging
docker run -it --rm \
  -p 8000:8000 \
  -e HF_DEBUG=true \
  ohfp-api:latest bash
```

## Best Practices

### Production Deployment

1. **Use specific image tags** instead of `latest`
2. **Set resource limits** for containers
3. **Use health checks** for container orchestration
4. **Implement log rotation** to prevent disk space issues
5. **Use secrets management** for sensitive configuration
6. **Regular security updates** for base images
7. **Monitor container metrics** and logs
8. **Backup persistent data** volumes

### Development Workflow

1. **Use Docker Compose** for local development
2. **Mount source code** as volumes for live reloading
3. **Use environment files** for configuration
4. **Test with different configurations** before production
5. **Use multi-stage builds** to optimize image size

### CI/CD Integration

```bash
# Build and test in CI
./docker/build.sh --version ${CI_COMMIT_TAG} --registry ${CI_REGISTRY}

# Security scanning
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy image ohfp-api:latest

# Push to registry
docker push ${CI_REGISTRY}/ohfp-api:${CI_COMMIT_TAG}
```
