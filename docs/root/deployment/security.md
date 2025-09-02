# Security Guide

## Overview

Security best practices for deploying the Open Host Factory Plugin in production environments.

## Authentication

### JWT Bearer Token

```json
{
  "server": {
    "auth": {
      "enabled": true,
      "strategy": "bearer_token",
      "bearer_token": {
        "secret_key": "your-very-secure-secret-key",
        "algorithm": "HS256",
        "token_expiry": 3600
      }
    }
  }
}
```

### AWS IAM Authentication

```json
{
  "server": {
    "auth": {
      "enabled": true,
      "strategy": "iam",
      "iam": {
        "region": "us-east-1",
        "required_actions": [
          "ec2:DescribeInstances",
          "ec2:RunInstances",
          "ec2:TerminateInstances"
        ]
      }
    }
  }
}
```

## Network Security

### HTTPS Configuration

```json
{
  "server": {
    "require_https": true,
    "trusted_hosts": [
      "your-domain.com",
      "api.your-domain.com"
    ]
  }
}
```

### CORS Configuration

```json
{
  "server": {
    "cors": {
      "enabled": true,
      "origins": [
        "https://your-domain.com"
      ],
      "methods": ["GET", "POST", "PUT", "DELETE"],
      "headers": ["Authorization", "Content-Type"],
      "credentials": true
    }
  }
}
```

## Container Security

### Non-Root Execution

The Docker container runs as a non-root user (`ohfp`) for security:

```dockerfile
RUN groupadd -r ohfp && useradd -r -g ohfp -s /bin/false ohfp
USER ohfp
```

### Security Options

```yaml
# Docker Compose
services:
  ohfp-api:
    security_opt:
      - no-new-privileges:true
    read_only: true
    tmpfs:
      - /tmp:noexec,nosuid,size=100m
```

## Secrets Management

### Environment Variables

```bash
# Use secure environment variables
HF_AUTH_BEARER_SECRET_KEY=your-secure-secret
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
```

### Docker Secrets

```yaml
# Docker Compose with secrets
services:
  ohfp-api:
    secrets:
      - jwt-secret
    environment:
      HF_AUTH_BEARER_SECRET_KEY_FILE: /run/secrets/jwt-secret

secrets:
  jwt-secret:
    external: true
```

### Kubernetes Secrets

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: ohfp-secrets
type: Opaque
data:
  jwt-secret: <base64-encoded-secret>
```

## Production Security Checklist

- [ ] Authentication enabled
- [ ] Strong JWT secret key
- [ ] HTTPS required
- [ ] Trusted hosts configured
- [ ] API documentation disabled in production
- [ ] Non-root container execution
- [ ] Resource limits set
- [ ] Network isolation configured
- [ ] Regular security updates
- [ ] Vulnerability scanning enabled

For complete security configuration, see the [deployment guide](readme.md).
