# Monitoring and Observability

## Overview

Comprehensive monitoring and observability for the Open Host Factory Plugin in production environments.

## Health Checks

### Basic Health Check

```bash
# Check application health
curl http://localhost:8000/health

# Expected response
{
  "status": "healthy",
  "service": "open-hostfactory-plugin",
  "version": "1.0.0",
  "timestamp": "2025-01-07T10:00:00Z"
}
```

### Kubernetes Health Checks

```yaml
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

## Metrics Collection

### Prometheus Integration

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'ohfp-api'
    static_configs:
      - targets: ['ohfp-api:8000']
    metrics_path: /metrics
    scrape_interval: 30s
```

### Custom Metrics

The application exposes metrics for:

- Request count and duration
- Authentication success/failure rates
- AWS API call metrics
- Error rates by endpoint
- Resource provisioning metrics

## Logging

### Structured Logging

```json
{
  "logging": {
    "level": "INFO",
    "format": "json",
    "file_enabled": true,
    "file_path": "/app/logs/app.log",
    "console_enabled": true
  }
}
```

### Log Aggregation

#### ELK Stack

```yaml
# Filebeat configuration
filebeat.inputs:
- type: log
  paths:
    - /app/logs/*.log
  fields:
    service: ohfp-api
  fields_under_root: true

output.elasticsearch:
  hosts: ["elasticsearch:9200"]
```

#### Fluentd

```conf
<source>
  @type tail
  path /app/logs/app.log
  pos_file /var/log/fluentd/ohfp.log.pos
  tag ohfp.api
  format json
</source>

<match ohfp.**>
  @type elasticsearch
  host elasticsearch
  port 9200
  index_name ohfp-logs
</match>
```

## Alerting

### Prometheus Alerts

```yaml
groups:
- name: ohfp-api
  rules:
  - alert: OHFPAPIDown
    expr: up{job="ohfp-api"} == 0
    for: 1m
    labels:
      severity: critical
    annotations:
      summary: "OHFP API is down"

  - alert: OHFPHighErrorRate
    expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.1
    for: 2m
    labels:
      severity: warning
    annotations:
      summary: "High error rate detected"
```

### AWS CloudWatch

```json
{
  "MetricName": "APIErrors",
  "Namespace": "OHFP/API",
  "Dimensions": [
    {
      "Name": "Service",
      "Value": "ohfp-api"
    }
  ],
  "Value": 1,
  "Unit": "Count"
}
```

## Dashboards

### Grafana Dashboard

Key metrics to monitor:

- Request rate and response time
- Error rates by endpoint
- Authentication success/failure
- AWS API call latency
- Resource provisioning success rate
- Container resource usage

### Sample Queries

```promql
# Request rate
rate(http_requests_total[5m])

# Error rate
rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m])

# Response time percentiles
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))
```

## Distributed Tracing

### OpenTelemetry

```python
from opentelemetry import trace
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Configure tracing
trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer(__name__)

jaeger_exporter = JaegerExporter(
    agent_host_name="jaeger",
    agent_port=6831,
)

span_processor = BatchSpanProcessor(jaeger_exporter)
trace.get_tracer_provider().add_span_processor(span_processor)
```

## Performance Monitoring

### Application Performance

Monitor:
- Memory usage and garbage collection
- CPU utilization
- Database connection pool usage
- AWS API rate limiting
- Cache hit rates

### Infrastructure Monitoring

Monitor:
- Container resource usage
- Network latency
- Disk I/O
- Load balancer metrics
- Auto-scaling events

For complete monitoring setup, see the [deployment guide](readme.md).
