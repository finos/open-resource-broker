# Cloud Platform Deployment

## Overview

The Open Resource Broker supports deployment on major cloud platforms with containerized and serverless options.

## Kubernetes Deployment

### Helm Chart

The chart in `deploy/helm/orb` is the supported way to run the REST server on Kubernetes.

```bash
helm install orb ./deploy/helm/orb --namespace orb --create-namespace
```

By default it runs a single replica with JSON storage on a PersistentVolumeClaim, uses the
`k8s` provider in the release namespace and serves the API and embedded UI under `/orb`. See
[deploy/helm/orb/README.md](https://github.com/finos/open-resource-broker/blob/main/deploy/helm/orb/README.md)
for all values.

Points that matter in production:

- **Storage.** `json` and `sqlite` keep state in files on one volume and allow a single writer,
  so the chart fixes `replicaCount` to 1, uses the `Recreate` update strategy and fails to render
  when `replicaCount` is greater than 1. The claim is kept when the release is uninstalled.
- **Health probes.** Liveness and startup use `/livez`; readiness uses `/readyz`. In embedded UI
  mode (the default) the paths are `/orb/livez` and `/orb/readyz`. Set `ui.mode` to `split` or
  `disabled` for API-only deployments, which serve the probes at the root.
- **Ingress.** Set `ingress.enabled` with hosts and optional `tls` entries. TLS ends at the
  ingress; the pod serves plain HTTP.
- **Container user.** The pod runs as the numeric uid and gid in `podSecurityContext`, which must
  match the user in the image.

### Basic Deployment

The manifest below shows the minimum without Helm. Use the `/livez` and `/readyz` probes; in the
default embedded UI mode prefix them with `/orb`.

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
        livenessProbe:
          httpGet:
            path: /orb/livez
            port: 8000
        readinessProbe:
          httpGet:
            path: /orb/readyz
            port: 8000
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



## OCI Compute

For a broker running on OCI Compute, use the
[OCI deployment and authentication guide](oci-integration.md). It covers
Python/runtime setup, instance-principal IAM, local profile authentication, and
the acquire/status/return canary. Worker provisioning is documented separately in
the [OCICompute provider reference](../providers/oci/compute.md).

For complete deployment examples and configuration, see the [main deployment guide](readme.md).
