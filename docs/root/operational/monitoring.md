# Monitoring Setup

This document provides guidance on setting up monitoring and alerting for the Open Resource Broker.

## Overview

Monitoring is essential for maintaining the health and performance of your Host Factory deployment. This guide covers:

- System monitoring setup
- Application metrics collection
- Alerting configuration
- Dashboard creation
- Log aggregation

## System Monitoring

### Health Checks

The plugin provides built-in health check endpoints:

```bash
# Check provider health
orb providers health

# Check system status
orb system status
```

### Metrics Collection

The plugin exposes metrics in Prometheus format:

- Request counts and latencies
- Provider operation metrics
- Storage operation metrics
- Error rates and types

### Log Monitoring

Configure log aggregation for:

- Application logs
- Provider operation logs
- Error logs and stack traces
- Audit logs

## Background Request Reconciliation

A long-running ORB server (REST API or daemon) can refresh in-flight requests
from their providers on a timer, so stored request and machine state follows the
provider without a client polling each request. List views serve stored state
that was refreshed recently instead of calling the provider again.

Configure it under `request` in the configuration file:

| Key | Default | Meaning |
|-----|---------|---------|
| `reconcile_enabled` | `false` | Set to `true` to run the loop. Off by default because it makes periodic provider API calls, one per in-flight request per pass. While off, list views always refresh from the provider. |
| `reconcile_interval_seconds` | `60` | Seconds between passes. |
| `reconcile_max_concurrency` | `4` | Most requests refreshed against providers at the same time. |
| `reconcile_request_timeout_seconds` | `30` | Time limit for refreshing one request. |
| `list_cache_max_age_seconds` | `30` | A request refreshed within this window is listed from stored state. `0` always refreshes. |

Requests of a provider whose circuit breaker is open are skipped until the
circuit closes, and provider failures seen by the loop count toward that
circuit. A failed provider call does not refresh a request's last-checked time.

Every write the loop makes re-reads the request first, so a request cancelled while its
provider call was running stays cancelled. With the SQL store a conflicting write is
detected and re-applied once; the JSON store has no version check, so a change that lands
in the few milliseconds between that read and the save can still be lost.

The loop runs only in a server with `server.workers` set to 1; with more
workers it is not started (a message is logged at startup), because each worker
would run its own loop against the same providers and storage. It starts with
the server; on shutdown calls already in flight get a few seconds to finish
before the pass is cancelled. One-shot CLI commands do not run it. The
list-view caching described above is per process.

## Alerting

Set up alerts for:

- High error rates
- Provider failures
- Storage issues
- Performance degradation

## Dashboard Examples

Create dashboards to monitor:

- Request volume and success rates
- Provider performance metrics
- Resource utilization
- Error trends

## Troubleshooting

Common monitoring issues and solutions:

- Metric collection failures
- Alert configuration problems
- Dashboard display issues

For detailed troubleshooting, see the [Troubleshooting Guide](../user_guide/troubleshooting.md).

## Related Documentation

- [Performance Tuning](performance.md)
- [Backup and Recovery](backup_recovery.md)
- [Tools and Utilities](tools.md)
