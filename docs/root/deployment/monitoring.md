# Monitoring and Observability

## Overview

ORB uses [OpenTelemetry](https://opentelemetry.io/) as its observability foundation.
Metrics, traces, and logs are emitted through the OTel API and exported via configurable
backends.  The `[monitoring]` install extra activates the full stack; a minimal install
without it runs without any metrics overhead (the OTel API no-op default).

## Installation

```bash
# Full observability stack (Prometheus scrape + OTLP push + FastAPI/botocore spans)
pip install orb-py[monitoring]

# AWS-specific (adds botocore instrumentation)
pip install orb-py[monitoring-aws]

# API-only embedding (OTel API, no SDK weight — for library consumers)
pip install orb-py[otel]
```

## Configuration

Observability is configured under the `observability` key in your ORB config file, or
via standard `OTEL_*` environment variables (env vars win over file values).

```yaml
# orb-config.yml
observability:
  enabled: true
  service_name: my-orb-instance          # OTEL_SERVICE_NAME also accepted
  metrics_exporters:
    - prometheus                          # scrape /metrics (REST server)
    - file                                # write JSONL (CLI commands)
  traces_exporter: file                  # "otlp" | "file" | null
  otlp_endpoint: http://collector:4317   # OTEL_EXPORTER_OTLP_ENDPOINT also accepted
  traces_sample_rate: 0.1                # OTEL_TRACES_SAMPLER_ARG also accepted
  telemetry_file_dir: /var/log/orb/telemetry  # ORB_TELEMETRY_FILE_DIR also accepted
```

### Metrics exporters

| Value | Effect |
|---|---|
| `"prometheus"` | Registers a `PrometheusMetricReader` on the global `prometheus_client.REGISTRY`; expose via the `/metrics` route. |
| `"otlp"` | Pushes via `PeriodicExportingMetricReader(OTLPMetricExporter(...))`. |
| `"file"` | Writes OTLP JSON Lines to `telemetry_file_dir/metrics.jsonl`. Mandatory for CLI commands — the process exits before any scrape fires. |

Multiple values can be listed simultaneously (e.g. `["prometheus", "file"]`).

### Auto-instrumentation toggles

All default to `true` when `enabled: true` and the package is installed.

| Toggle | Package required | What it instruments |
|---|---|---|
| `instrument_fastapi` | `opentelemetry-instrumentation-fastapi` | HTTP request spans + `http.server.request.duration` histogram, `http.server.active_requests` gauge |
| `instrument_sqlalchemy` | `opentelemetry-instrumentation-sqlalchemy` | Database query spans |
| `instrument_botocore` | `opentelemetry-instrumentation-botocore` | AWS API call spans (complements `BotocoreMetricsHandler`) |
| `instrument_click` | `opentelemetry-instrumentation-click` | Span per CLI command invocation |
| `instrument_system_metrics` | `opentelemetry-instrumentation-system-metrics` | CPU, memory, GC, thread gauges |
| `instrument_logging` | `opentelemetry-instrumentation-logging` | Injects `trace_id`/`span_id` into log records |

## Per-surface export model

ORB runs as four distinct surfaces with different process lifecycles:

| Surface | Recommended exporters | Notes |
|---|---|---|
| REST server (`uvicorn`) | `["prometheus"]` or `["prometheus", "otlp"]` | Long-lived; scrape works. Multiprocess caveat: each uvicorn worker has its own in-process REGISTRY; only one worker's metrics are exposed per scrape when `workers > 1`. |
| CLI (`orb` commands) | `["file"]` | Short-lived; `MeterProvider.shutdown()` is called at process exit to flush all pending data before termination. |
| MCP server | `["otlp"]` or `["file"]` | OTLP push is the safe default regardless of transport. |
| SDK (embedded library) | Host application supplies its own `MeterProvider` | ORB uses OTel API internally; the host wires the provider. |

## What is instrumented

### Domain / application metrics (OTel Meter, bridged to Prometheus)

| OTel instrument name | Prometheus name | Type | Description |
|---|---|---|---|
| `orb.requests.pending` | `orb_requests_pending` | UpDownCounter | Requests in-flight |
| `orb.requests.total` | `orb_requests_total` | Counter | Completed requests |
| `orb.active.instances` | `orb_active_instances` | UpDownCounter | Active machine instances |
| `orb.provisioning.duration` | `orb_provisioning_duration_seconds` | Histogram | End-to-end provisioning wall-clock |
| `orb.requests.failed.total` | `orb_requests_failed_total` | Counter | Failed requests |

### AWS provider metrics (via `BotocoreMetricsHandler`)

Per-service/operation labelled counters and histograms for AWS API calls, errors,
throttles, retries, and payload sizes.  15 metric names total.

### k8s provider metrics (OTel Meter, bridged to Prometheus)

`orb_k8s_acquire_total`, `orb_k8s_release_total`, `orb_k8s_pod_startup_seconds`,
`orb_k8s_poll_total`, `orb_k8s_watch_reconnect_total`, `orb_k8s_pod_creation_total`,
`orb_k8s_active_pods`, `orb_k8s_active_requests`, `orb_k8s_apiserver_latency_seconds`,
`orb_k8s_circuit_breaker_state`.

### Storage operation metrics (OTel Meter)

Per-operation labelled counters and histograms for storage backend calls.

### FastAPI auto-instrumentation (when `instrument_fastapi: true`)

`http_server_request_duration_seconds` (histogram) and `http_server_active_requests`
(gauge) per route; request spans for distributed tracing.  The `/health` and `/metrics`
routes are excluded from instrumentation to avoid self-telemetry noise.

## Endpoints

### `GET /metrics`

Serves Prometheus text format.  Includes:

- OTel SDK metrics bridged via `PrometheusMetricReader`
- Native `prometheus_client` metrics (k8s `K8sMetrics`, process collectors)
- Python process metrics (`python_gc_*`, `python_info`, etc.)

```bash
curl http://localhost:8000/metrics
```

Prometheus scrape configuration:

```yaml
scrape_configs:
  - job_name: orb-api
    static_configs:
      - targets: [orb-api:8000]
    metrics_path: /metrics
    scrape_interval: 30s
```

### `GET /api/v1/observability/telemetry`

Read-only status endpoint.  Returns the current OTel configuration — enabled state,
active exporters, service name, sampler rate, and which instrumentors are active.
Requires `viewer` role.

```bash
curl http://localhost:8000/api/v1/observability/telemetry
```

Example response (telemetry enabled):

```json
{
  "enabled": true,
  "service_name": "orb",
  "metrics_exporters": ["prometheus"],
  "traces_exporter": null,
  "traces_sample_rate": 0.1,
  "instrumentors": {
    "fastapi": true,
    "sqlalchemy": true,
    "botocore": true,
    "click": true,
    "system_metrics": true,
    "logging": true
  }
}
```

When telemetry is disabled:

```json
{"enabled": false}
```

### `GET /health`

Application health check; see [health documentation](../operational/health.md).

## Traces

When `traces_exporter` is set, ORB emits distributed traces.

| Backend | Config |
|---|---|
| OTLP (Jaeger / Grafana Tempo / AWS X-Ray via ADOT) | `traces_exporter: otlp` + `otlp_endpoint: http://collector:4317` |
| File (offline / debug) | `traces_exporter: file` + optionally `telemetry_file_dir: /path` |

Traces include:
- FastAPI request spans (when `instrument_fastapi: true`)
- AWS botocore spans (when `instrument_botocore: true`)
- SQLAlchemy query spans (when `instrument_sqlalchemy: true`)
- Click CLI command spans (when `instrument_click: true`)

W3C TraceContext propagation is active; `X-Amzn-Trace-Id` is also propagated
(via `opentelemetry-propagator-aws-xray`).

## Logging

When `instrument_logging: true`, the `LoggingInstrumentor` injects `trace_id` and
`span_id` into every Python `logging` log record as extra fields.  Any log aggregator
that ingests both traces and logs (e.g. CloudWatch Insights, Grafana Loki) can use
these fields to correlate log lines with the originating trace.

## Alerting (Prometheus)

```yaml
groups:
  - name: orb-api
    rules:
      - alert: ORBAPIDown
        expr: up{job="orb-api"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: ORB API is down

      - alert: ORBHighProvisioningFailureRate
        expr: |
          rate(orb_requests_failed_total[5m]) /
          rate(orb_requests_total[5m]) > 0.05
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: High provisioning failure rate (> 5%)

      - alert: ORBHighResponseLatency
        expr: |
          histogram_quantile(0.95,
            rate(http_server_request_duration_seconds_bucket[5m])
          ) > 2.0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: p95 response time > 2s
```

## Grafana dashboard queries

```promql
# Active provisioning requests
orb_requests_pending

# Provisioning throughput (req/min)
rate(orb_requests_total[5m]) * 60

# p95 provisioning duration
histogram_quantile(0.95, rate(orb_provisioning_duration_seconds_bucket[5m]))

# HTTP request rate by route
rate(http_server_request_duration_seconds_count[5m])

# AWS API throttle rate
rate(orb_aws_throttle_total[5m])
```

## SDK usage guide

ORB library code calls OTel **API** instruments only (`meter.create_counter(...)` etc.).
The host application controls which backend receives the data by configuring the OTel
global `MeterProvider`.

Minimal example for an embedded consumer:

```python
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry import metrics

# Wire OTel global before importing ORB components
reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint="http://collector:4317"))
provider = MeterProvider(metric_readers=[reader])
metrics.set_meter_provider(provider)

# ORB instruments now emit to the above provider
from orb.sdk import ORBClient
client = ORBClient(...)
```

For file-based offline use:

```python
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry import metrics

reader = PeriodicExportingMetricReader(ConsoleMetricExporter())
provider = MeterProvider(metric_readers=[reader])
metrics.set_meter_provider(provider)
# Call provider.shutdown() when done to flush pending data.
```

> **Note:** Frontend / browser-side observability (RUM, browser OTel JS) is a
> separate workstream and is not covered by this guide.
