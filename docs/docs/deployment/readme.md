# Deployment Guide

## Overview

The Open Host Factory Plugin supports multiple deployment methods, with Docker containerization being the recommended approach for production deployments.

## Deployment Methods

### [Docker] Docker Containerization (Recommended)

**Production-ready containerization with enterprise features:**

- **Multi-stage builds** for optimized image size and security
- **Security hardening** with non-root user execution
- **Multi-architecture support** (AMD64, ARM64)
- **Comprehensive configuration** via environment variables
- **Health checks** and monitoring integration
- **Professional logging** and error handling

**Quick Start:**
```bash
# Development
cp .env.example .env
docker-compose up -d

# Production
docker-compose -f docker-compose.prod.yml up -d
```

**[Complete Docker Documentation ->](docker.md)**

### [Cloud] Cloud Platform Deployment

#### AWS ECS/Fargate
```bash
# Build and push to ECR
./docker/build.sh --registry 123456789012.dkr.ecr.us-east-1.amazonaws.com --push

# Create ECS task definition
aws ecs register-task-definition --cli-input-json file://ecs-task-definition.json

# Deploy service
aws ecs update-service --cluster production --service ohfp-api --force-new-deployment
```

#### Kubernetes
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ohfp-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: ohfp-api
  template:
    metadata:
      labels:
        app: ohfp-api
    spec:
      containers:
      - name: ohfp-api
        image: your-registry.com/ohfp-api:1.0.0
        ports:
        - containerPort: 8000
        env:
        - name: HF_SERVER_ENABLED
          value: "true"
        - name: HF_AUTH_ENABLED
          value: "true"
        - name: HF_AUTH_STRATEGY
          value: "bearer_token"
        - name: HF_AUTH_BEARER_SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: ohfp-secrets
              key: jwt-secret
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
```

#### Google Cloud Run
```bash
# Build and push to Google Container Registry
docker build -t gcr.io/your-project/ohfp-api:latest .
docker push gcr.io/your-project/ohfp-api:latest

# Deploy to Cloud Run
gcloud run deploy ohfp-api \
  --image gcr.io/your-project/ohfp-api:latest \
  --platform managed \
  --region us-central1 \
  --set-env-vars HF_SERVER_ENABLED=true,HF_AUTH_ENABLED=true
```

### [Server] Traditional Server Deployment

#### Direct Installation
```bash
# Install from PyPI
pip install open-hostfactory-plugin

# Or install from source
git clone <repository-url>
cd open-hostfactory-plugin
pip install -e .

# Configure
cp config/default_config.json config/production.json
# Edit config/production.json

# Start server
ohfp system serve --host 0.0.0.0 --port 8000 --config config/production.json
```

#### Systemd Service
```ini
# /etc/systemd/system/ohfp-api.service
[Unit]
Description=Open Host Factory Plugin REST API
After=network.target

[Service]
Type=simple
User=ohfp
Group=ohfp
WorkingDirectory=/opt/ohfp
Environment=HF_SERVER_ENABLED=true
Environment=HF_AUTH_ENABLED=true
ExecStart=/opt/ohfp/.venv/bin/python src/run.py system serve --config config/production.json
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Configuration Management

### Environment Variables

**Complete configuration via environment variables:**

```bash
# Server Configuration
HF_SERVER_ENABLED=true
HF_SERVER_HOST=0.0.0.0
HF_SERVER_PORT=8000
HF_SERVER_WORKERS=4
HF_SERVER_LOG_LEVEL=info
HF_SERVER_DOCS_ENABLED=false  # Disable in production

# Authentication Configuration
HF_AUTH_ENABLED=true
HF_AUTH_STRATEGY=bearer_token
HF_AUTH_BEARER_SECRET_KEY=your-very-secure-secret-key
HF_AUTH_BEARER_TOKEN_EXPIRY=3600

# AWS Provider Configuration
HF_PROVIDER_TYPE=aws
HF_PROVIDER_AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key

# Storage Configuration
HF_STORAGE_STRATEGY=json
HF_STORAGE_BASE_PATH=/app/data

# Logging Configuration
HF_LOGGING_LEVEL=INFO
HF_LOGGING_CONSOLE_ENABLED=true
HF_LOGGING_FILE_ENABLED=true
HF_LOGGING_FILE_PATH=/app/logs/app.log

# Security Configuration
HF_SERVER_REQUIRE_HTTPS=true
HF_SERVER_TRUSTED_HOSTS=your-domain.com,api.your-domain.com
```

### Configuration Files

**Configuration precedence (highest to lowest):**

1. **Environment variables** (highest precedence)
2. **Docker-specific config**: `config/docker.json`
3. **Production config**: `config/production.json`
4. **Default config**: `config/default_config.json`

### Secrets Management

#### Docker Secrets
```bash
# Create secret
echo "your-jwt-secret" | docker secret create ohfp-jwt-secret -

# Use in Docker Compose
services:
  ohfp-api:
    secrets:
      - ohfp-jwt-secret
    environment:
      HF_AUTH_BEARER_SECRET_KEY_FILE: /run/secrets/ohfp-jwt-secret
```

#### Kubernetes Secrets
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: ohfp-secrets
type: Opaque
data:
  jwt-secret: <base64-encoded-secret>
  aws-access-key: <base64-encoded-key>
  aws-secret-key: <base64-encoded-secret>
```

#### AWS Secrets Manager
```bash
# Store secret
aws secretsmanager create-secret \
  --name ohfp/jwt-secret \
  --secret-string "your-jwt-secret"

# Use in ECS task definition
{
  "secrets": [
    {
      "name": "HF_AUTH_BEARER_SECRET_KEY",
      "valueFrom": "arn:aws:secretsmanager:region:account:secret:ohfp/jwt-secret"
    }
  ]
}
```

## Security Considerations

### Production Security Checklist

- [ ] **Authentication enabled**: `HF_AUTH_ENABLED=true`
- [ ] **Strong JWT secret**: Use cryptographically secure random key
- [ ] **HTTPS required**: `HF_SERVER_REQUIRE_HTTPS=true`
- [ ] **Trusted hosts configured**: Limit to your domains
- [ ] **API docs disabled**: `HF_SERVER_DOCS_ENABLED=false`
- [ ] **Non-root execution**: Container runs as `ohfp` user
- [ ] **Resource limits**: Set CPU and memory limits
- [ ] **Network isolation**: Use private networks where possible
- [ ] **Regular updates**: Keep base images and dependencies updated
- [ ] **Security scanning**: Regular vulnerability assessments

### Network Security

```bash
# Docker network isolation
docker network create --driver bridge ohfp-private
docker run --network ohfp-private ohfp-api:latest

# Kubernetes network policies
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: ohfp-api-policy
spec:
  podSelector:
    matchLabels:
      app: ohfp-api
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: nginx-ingress
    ports:
    - protocol: TCP
      port: 8000
```

## Monitoring and Observability

### Health Checks

```bash
# Basic health check
curl http://localhost:8000/health

# Detailed system information
curl http://localhost:8000/info

# Kubernetes liveness probe
livenessProbe:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 30
  periodSeconds: 10
```

### Logging

**Structured logging configuration:**

```json
{
  "logging": {
    "level": "INFO",
    "file_path": "/app/logs/app.log",
    "console_enabled": true,
    "file_enabled": true,
    "max_file_size": "50MB",
    "backup_count": 10,
    "format": "json"
  }
}
```

### Metrics Collection

**Prometheus integration:**

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'ohfp-api'
    static_configs:
      - targets: ['ohfp-api:8000']
    metrics_path: /metrics
    scrape_interval: 30s
```

## Scaling and Load Balancing

### Horizontal Scaling

#### Docker Compose
```bash
# Scale to 3 replicas
docker-compose up -d --scale ohfp-api=3
```

#### Kubernetes
```yaml
apiVersion: apps/v1
kind: Deployment
spec:
  replicas: 5
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 1
```

#### AWS ECS
```bash
# Update service desired count
aws ecs update-service \
  --cluster production \
  --service ohfp-api \
  --desired-count 5
```

### Load Balancing

#### Nginx Configuration
```nginx
upstream ohfp_backend {
    server ohfp-api-1:8000;
    server ohfp-api-2:8000;
    server ohfp-api-3:8000;
}

server {
    listen 80;
    server_name api.your-domain.com;

    location / {
        proxy_pass http://ohfp_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /health {
        proxy_pass http://ohfp_backend;
        access_log off;
    }
}
```

## Backup and Disaster Recovery

### Data Backup

```bash
# Backup data volumes
docker run --rm -v ohfp-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/ohfp-data-backup-$(date +%Y%m%d).tar.gz -C /data .

# Backup configuration
cp -r config/ backups/config-$(date +%Y%m%d)/
```

### Database Backup (if using PostgreSQL)

```bash
# PostgreSQL backup
docker exec ohfp-postgres pg_dump -U ohfp ohfp > backup-$(date +%Y%m%d).sql

# Restore
docker exec -i ohfp-postgres psql -U ohfp ohfp < backup-20250107.sql
```

## Troubleshooting

### Common Issues

**Container won't start:**
```bash
# Check logs
docker logs container-name

# Check configuration
docker exec container-name env | grep HF_

# Test configuration
docker run --rm -it ohfp-api:latest bash
```

**Performance issues:**
```bash
# Check resource usage
docker stats

# Check application metrics
curl http://localhost:8000/metrics

# Increase workers
docker run -e HF_SERVER_WORKERS=4 ohfp-api:latest
```

**Authentication problems:**
```bash
# Test authentication
curl -H "Authorization: Bearer your-token" http://localhost:8000/info

# Check JWT configuration
docker exec container-name python -c "
from src.config.manager import ConfigurationManager
config = ConfigurationManager()
print(config.get_typed(ServerConfig).auth.enabled)
"
```

### Debug Mode

```bash
# Enable debug mode
docker run -e HF_DEBUG=true -e HF_LOGGING_LEVEL=DEBUG ohfp-api:latest

# Interactive debugging
docker run -it --rm ohfp-api:latest bash
```

## Performance Optimization

### Resource Allocation

```yaml
# Kubernetes resource requests/limits
resources:
  requests:
    memory: "512Mi"
    cpu: "500m"
  limits:
    memory: "2Gi"
    cpu: "2000m"
```

### Application Tuning

```bash
# Increase worker processes
HF_SERVER_WORKERS=4

# Optimize for high concurrency
HF_SERVER_WORKER_CLASS=uvicorn.workers.UvicornWorker
HF_SERVER_WORKER_CONNECTIONS=1000
```

## Migration Guide

### From Traditional Deployment to Docker

1. **Export configuration**:
   ```bash
   # Create environment file from existing config
   python scripts/config-to-env.py config/production.json > .env
   ```

2. **Test Docker deployment**:
   ```bash
   docker-compose up -d
   ```

3. **Migrate data**:
   ```bash
   # Copy existing data
   docker cp /old/data/path container-name:/app/data/
   ```

4. **Update DNS/Load Balancer**:
   ```bash
   # Point traffic to new Docker deployment
   ```

### Version Upgrades

```bash
# Pull new image
docker pull ohfp-api:latest

# Rolling update
docker-compose up -d --no-deps ohfp-api

# Kubernetes rolling update
kubectl set image deployment/ohfp-api ohfp-api=ohfp-api:latest
```

This comprehensive deployment guide covers all major deployment scenarios and operational concerns for the Open Host Factory Plugin REST API.
