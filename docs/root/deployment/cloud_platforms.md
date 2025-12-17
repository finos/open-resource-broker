# Cloud Platform Deployment

## Overview

The Open Host Factory Plugin supports deployment on major cloud platforms with containerized and serverless options.

## Kubernetes Deployment

### Basic Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: orb-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: orb-api
  template:
    metadata:
      labels:
        app: orb-api
    spec:
      containers:
      - name: orb-api
        image: your-registry.com/orb-api:1.0.0
        ports:
        - containerPort: 8000
        env:
        - name: HF_SERVER_ENABLED
          value: "true"
        - name: HF_AUTH_ENABLED
          value: "true"
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
```

## AWS ECS/Fargate

### Task Definition

```json
{
  "family": "orb-api",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "arn:aws:iam::account:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::account:role/orb-task-role",
  "containerDefinitions": [
    {
      "name": "orb-api",
      "image": "your-registry.com/orb-api:latest",
      "portMappings": [
        {
          "containerPort": 8000,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "HF_SERVER_ENABLED",
          "value": "true"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/orb-api",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      }
    }
  ]
}
```

## Google Cloud Run

```bash
# Deploy to Cloud Run
gcloud run deploy orb-api \
  --image gcr.io/your-project/orb-api:latest \
  --platform managed \
  --region us-central1 \
  --set-env-vars HF_SERVER_ENABLED=true,HF_AUTH_ENABLED=true \
  --allow-unauthenticated
```

## Azure Container Instances

```bash
# Deploy to Azure Container Instances
az container create \
  --resource-group myResourceGroup \
  --name orb-api \
  --image your-registry.com/orb-api:latest \
  --ports 8000 \
  --environment-variables HF_SERVER_ENABLED=true HF_AUTH_ENABLED=true
```

For complete deployment examples and configuration, see the [main deployment guide](readme.md).
