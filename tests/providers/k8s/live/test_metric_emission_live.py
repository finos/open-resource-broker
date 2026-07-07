"""Live integration tests for T24: metric emission via Prometheus scrape.

Tests in this module hit a real Kubernetes cluster with a Prometheus stack.
They are skipped by default; pass ``--run-k8s`` to enable them.

Scenario: ORB emits provider metrics (acquire_count, release_count,
active_resources, error_count) during normal operation.  After a complete
acquire→release cycle the Prometheus scrape endpoint must expose non-zero
counters for the k8s provider.

Infrastructure requirement:
- A Prometheus scrape endpoint reachable from the test runner.  Configured
  via the ``ORB_PROMETHEUS_SCRAPE_URL`` environment variable or the ORB
  config ``metrics.prometheus_url`` key.  Typical value:
  ``http://localhost:9090/metrics`` (port-forward from the cluster).
- Tests are skipped when the scrape URL is absent or unreachable.
"""

from __future__ import annotations

import logging
import os
import time
import urllib.request
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.metric_emission")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_METRICS_SCRAPE_ENV = "ORB_PROMETHEUS_SCRAPE_URL"
_SCRAPE_TIMEOUT = 5  # seconds
_POLL_INTERVAL = 3  # seconds
_METRIC_SETTLE_WAIT = 10  # seconds for metrics to propagate after operation


# ---------------------------------------------------------------------------
# Skip helpers
# ---------------------------------------------------------------------------


def _get_scrape_url(orb_config: dict) -> str | None:
    """Return the Prometheus scrape URL from env or ORB config."""
    env_url = os.environ.get(_METRICS_SCRAPE_ENV)
    if env_url:
        return env_url
    metrics_cfg = orb_config.get("metrics", {})
    return metrics_cfg.get("prometheus_url") or metrics_cfg.get("scrape_url")


def _scrape_reachable(url: str) -> bool:
    """Return True when the scrape URL responds with HTTP 200."""
    try:
        with urllib.request.urlopen(url, timeout=_SCRAPE_TIMEOUT) as resp:
            return resp.status == 200
    except Exception as exc:
        log.debug("Prometheus scrape URL %r unreachable: %s", url, exc)
        return False


def _parse_metric_value(
    scrape_text: str, metric_name: str, labels: dict[str, str] | None = None
) -> float | None:
    """Extract the float value of a Prometheus metric line from scraped text.

    Parses text-format Prometheus exposition.  Returns the first matching
    sample value, or None when the metric is absent.
    """
    for line in scrape_text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if not line.startswith(metric_name):
            continue
        if labels:
            if not all(f'{k}="{v}"' in line for k, v in labels.items()):
                continue
        parts = line.split()
        if len(parts) >= 2:
            try:
                return float(parts[-1])
            except ValueError:
                continue
    return None


def _fetch_metrics(url: str) -> str:
    """Fetch raw Prometheus exposition text from *url*."""
    with urllib.request.urlopen(url, timeout=_SCRAPE_TIMEOUT) as resp:
        return resp.read().decode("utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_k8s_client(k8s_provider_config: dict):
    """Build a live K8sClient from the ORB provider config."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    config = K8sProviderConfig(
        namespace=k8s_provider_config.get("namespace"),
        kubeconfig_path=k8s_provider_config.get("kubeconfig_path"),
        context=k8s_provider_config.get("context"),
        in_cluster=k8s_provider_config.get("in_cluster"),
    )
    logger = MagicMock()
    client = K8sClient(config=config, logger=logger)
    client.load_config()
    return client, config


def _make_pod_handler(k8s_provider_config: dict):
    """Construct a live K8sPodHandler."""
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

    client, config = _build_k8s_client(k8s_provider_config)
    logger = MagicMock()
    return K8sPodHandler(kubernetes_client=client, config=config, logger=logger), config


def _make_request(request_id: str, count: int = 1):
    """Construct a minimal Request."""
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="live-tpl",
        requested_count=count,
        provider_data={},
    )


def _make_template(namespace: str):
    """Build a minimal Template."""
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="live-tpl",
        provider_type="k8s",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=10,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "command": ["sh", "-c", "sleep 3600"],
            }
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_acquire_increments_metric_counter(
    k8s_provider_config: dict,
    k8s_live_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T24a: acquire_count metric increments after a successful acquire.

    Reads the baseline counter value, performs one acquire, waits for metric
    propagation, then asserts the counter has increased by at least 1.
    Skips when the Prometheus scrape endpoint is not configured or reachable.
    """
    scrape_url = _get_scrape_url(k8s_live_config)
    if not scrape_url:
        pytest.skip(
            f"Prometheus scrape URL not configured. "
            f"Set {_METRICS_SCRAPE_ENV} env var or metrics.prometheus_url in ORB config."
        )
    if not _scrape_reachable(scrape_url):
        pytest.skip(
            f"Prometheus scrape URL {scrape_url!r} is not reachable. "
            "Ensure Prometheus is running and accessible from the test runner "
            "(e.g. kubectl port-forward svc/prometheus 9090:9090)."
        )

    baseline_text = _fetch_metrics(scrape_url)
    baseline = (
        _parse_metric_value(
            baseline_text,
            "orb_k8s_acquire_total",
            labels={"provider": "k8s"},
        )
        or 0.0
    )

    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_template(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    pod_names = result.get("machine_ids", [])

    # Wait for metric scrape to refresh.
    time.sleep(_METRIC_SETTLE_WAIT)

    after_text = _fetch_metrics(scrape_url)
    after_value = _parse_metric_value(
        after_text,
        "orb_k8s_acquire_total",
        labels={"provider": "k8s"},
    )

    # Cleanup.
    if pod_names:
        try:
            await handler.release_hosts(pod_names, request.provider_data)
        except Exception as exc:
            log.warning("Cleanup release failed: %s", exc)

    assert after_value is not None, (
        "Metric orb_k8s_acquire_total not found in Prometheus scrape output. "
        "Ensure ORB is configured to emit k8s provider metrics."
    )
    assert after_value > baseline, (
        f"Expected orb_k8s_acquire_total to increase from {baseline} after acquire, "
        f"got {after_value}"
    )


async def test_release_increments_metric_counter(
    k8s_provider_config: dict,
    k8s_live_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T24b: release_count metric increments after a successful release.

    Acquires a pod, reads the release baseline, releases the pod, waits for
    scrape propagation, then asserts the release counter increased.
    """
    scrape_url = _get_scrape_url(k8s_live_config)
    if not scrape_url:
        pytest.skip(
            f"Prometheus scrape URL not configured. "
            f"Set {_METRICS_SCRAPE_ENV} env var or metrics.prometheus_url in ORB config."
        )
    if not _scrape_reachable(scrape_url):
        pytest.skip(f"Prometheus scrape URL {scrape_url!r} is not reachable.")

    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_template(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    pod_names = result.get("machine_ids", [])

    baseline_text = _fetch_metrics(scrape_url)
    baseline = (
        _parse_metric_value(
            baseline_text,
            "orb_k8s_release_total",
            labels={"provider": "k8s"},
        )
        or 0.0
    )

    if pod_names:
        await handler.release_hosts(pod_names, request.provider_data)

    time.sleep(_METRIC_SETTLE_WAIT)

    after_text = _fetch_metrics(scrape_url)
    after_value = _parse_metric_value(
        after_text,
        "orb_k8s_release_total",
        labels={"provider": "k8s"},
    )

    assert after_value is not None, (
        "Metric orb_k8s_release_total not found in Prometheus scrape output."
    )
    assert after_value > baseline, (
        f"Expected orb_k8s_release_total to increase from {baseline} after release, "
        f"got {after_value}"
    )


async def test_active_resources_gauge_reflects_state(
    k8s_provider_config: dict,
    k8s_live_config: dict,
    k8s_namespace: str,
    k8s_core_v1,
    live_request_id: str,
) -> None:
    """T24c: active_resources gauge increases on acquire and decreases on release.

    The gauge must be positive after acquire and lower (or zero) after release.
    Skips when the scrape endpoint is absent.
    """
    scrape_url = _get_scrape_url(k8s_live_config)
    if not scrape_url:
        pytest.skip(
            f"Prometheus scrape URL not configured. "
            f"Set {_METRICS_SCRAPE_ENV} env var or metrics.prometheus_url in ORB config."
        )
    if not _scrape_reachable(scrape_url):
        pytest.skip(f"Prometheus scrape URL {scrape_url!r} is not reachable.")

    handler, _ = _make_pod_handler(k8s_provider_config)
    request = _make_request(live_request_id, count=1)
    template = _make_template(k8s_namespace)

    result = await handler.acquire_hosts(request, template)
    pod_names = result.get("machine_ids", [])
    time.sleep(_METRIC_SETTLE_WAIT)

    after_acquire_text = _fetch_metrics(scrape_url)
    after_acquire_value = _parse_metric_value(
        after_acquire_text,
        "orb_k8s_active_resources",
        labels={"provider": "k8s"},
    )

    if pod_names:
        await handler.release_hosts(pod_names, request.provider_data)
    time.sleep(_METRIC_SETTLE_WAIT)

    after_release_text = _fetch_metrics(scrape_url)
    after_release_value = _parse_metric_value(
        after_release_text,
        "orb_k8s_active_resources",
        labels={"provider": "k8s"},
    )

    if after_acquire_value is not None:
        assert after_acquire_value >= 1, (
            f"Expected active_resources >= 1 after acquire, got {after_acquire_value}"
        )
    if after_release_value is not None and after_acquire_value is not None:
        assert after_release_value < after_acquire_value, (
            f"Expected active_resources to decrease after release: "
            f"acquire={after_acquire_value}, release={after_release_value}"
        )
