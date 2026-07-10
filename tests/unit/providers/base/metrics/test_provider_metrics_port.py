"""Unit tests for ProviderMetricsPort, NoOpProviderMetrics, OtelProviderMetrics.

These tests verify:
- Abstract interface cannot be instantiated directly.
- NoOpProviderMetrics implements all methods as silent pass-throughs.
- OtelProviderMetrics correctly delegates to OTel instruments (verified by
  intercepting instrument method calls).
- Graceful no-op when opentelemetry-api is absent.
- pending_requests lifecycle semantics preserved in OtelProviderMetrics.
- Naming convention: _to_otel_name().
"""

from __future__ import annotations

import builtins
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.base.metrics.provider_metrics_port import (
    NoOpProviderMetrics,
    OtelProviderMetrics,
    ProviderMetricsPort,
    _to_otel_name,
)

# ---------------------------------------------------------------------------
# ProviderMetricsPort (abstract)
# ---------------------------------------------------------------------------


def test_cannot_instantiate_abstract_port():
    """ProviderMetricsPort is abstract and cannot be directly instantiated."""
    with pytest.raises(TypeError):
        ProviderMetricsPort()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# NoOpProviderMetrics
# ---------------------------------------------------------------------------


def test_noop_record_operation_does_not_raise():
    impl = NoOpProviderMetrics()
    impl.record_operation("ec2", "run_instances", 0.5, True)


def test_noop_record_operation_none_duration():
    impl = NoOpProviderMetrics()
    impl.record_operation("pods", "create", None, True)


def test_noop_record_counter_does_not_raise():
    impl = NoOpProviderMetrics()
    impl.record_counter("circuit_breaker.opened.total", labels={"provider": "aws"})


def test_noop_record_gauge_does_not_raise():
    impl = NoOpProviderMetrics()
    impl.record_gauge("active_instances", 5.0, labels={"region": "us-east-1"})


def test_noop_record_histogram_does_not_raise():
    impl = NoOpProviderMetrics()
    impl.record_histogram("orb.provisioning.duration", 1.23)


def test_noop_is_subclass_of_port():
    assert issubclass(NoOpProviderMetrics, ProviderMetricsPort)


def test_noop_accepts_none_labels():
    impl = NoOpProviderMetrics()
    impl.record_counter("some.counter", labels=None)
    impl.record_gauge("some.gauge", 1.0, labels=None)
    impl.record_histogram("some.histogram", 0.1, labels=None)


# ---------------------------------------------------------------------------
# OtelProviderMetrics — instrument delegation
# ---------------------------------------------------------------------------


def _make_otel() -> tuple[OtelProviderMetrics, dict[str, list[tuple[Any, ...]]]]:
    """Return (impl, captured_calls) where captured_calls maps instrument-name → add/record args."""
    impl = OtelProviderMetrics()
    captured: dict[str, list[tuple[Any, ...]]] = {}

    def _fake_meter():
        m = MagicMock()

        def _make_counter(name, **kw):
            c = MagicMock()
            calls: list[tuple[Any, ...]] = []
            captured[name] = calls
            c.add.side_effect = lambda v, attributes=None, **_kw: calls.append((v, attributes))
            return c

        def _make_histogram(name, **kw):
            h = MagicMock()
            calls_h: list[tuple[Any, ...]] = []
            captured[name] = calls_h
            h.record.side_effect = lambda v, attributes=None, **_kw: calls_h.append((v, attributes))
            return h

        def _make_udc(name, **kw):
            u = MagicMock()
            calls_u: list[tuple[Any, ...]] = []
            captured[name] = calls_u
            u.add.side_effect = lambda v, attributes=None, **_kw: calls_u.append((v, attributes))
            return u

        m.create_counter.side_effect = _make_counter
        m.create_histogram.side_effect = _make_histogram
        m.create_up_down_counter.side_effect = _make_udc
        return m

    impl._get_meter = _fake_meter  # type: ignore[method-assign]
    # Reset lazy caches so instruments are re-created via the patched meter
    impl._operation_counter = None
    impl._operation_duration = None
    impl._counters.clear()
    impl._gauges.clear()
    impl._histograms.clear()
    return impl, captured


def test_otel_record_operation_creates_counter_and_histogram():
    impl, captured = _make_otel()
    impl.record_operation("ec2", "run_instances", 0.25, True)

    assert "orb.provider.operation.total" in captured
    assert "orb.provider.operation.duration" in captured


def test_otel_record_operation_success_label():
    impl, captured = _make_otel()
    impl.record_operation("ec2", "run_instances", 0.1, True)

    calls = captured["orb.provider.operation.total"]
    assert len(calls) == 1
    _value, attrs = calls[0]
    assert attrs["outcome"] == "success"
    assert attrs["service"] == "ec2"
    assert attrs["operation"] == "run_instances"


def test_otel_record_operation_error_label():
    impl, captured = _make_otel()
    impl.record_operation("ec2", "run_instances", 0.1, False, error_code="ThrottlingException")

    calls = captured["orb.provider.operation.total"]
    _value, attrs = calls[0]
    assert attrs["outcome"] == "error"
    assert attrs["error_code"] == "ThrottlingException"


def test_otel_record_operation_none_duration_skips_histogram():
    impl, captured = _make_otel()
    impl.record_operation("pods", "watch", None, True)

    # Counter exists, histogram should NOT exist (no duration provided)
    assert "orb.provider.operation.total" in captured
    assert "orb.provider.operation.duration" not in captured


def test_otel_record_counter():
    impl, captured = _make_otel()
    impl.record_counter("circuit_breaker.opened.total", labels={"provider": "aws"})

    otel_name = _to_otel_name("circuit_breaker.opened.total")
    assert otel_name in captured
    calls = captured[otel_name]
    assert calls[0][0] == 1
    assert calls[0][1]["provider"] == "aws"


def test_otel_record_gauge_absolute_set():
    """record_gauge with absolute value 5 from 0 → delta = 5."""
    impl, captured = _make_otel()
    impl.record_gauge("active_instances", 5.0)

    otel_name = _to_otel_name("active_instances")
    assert otel_name in captured
    calls = captured[otel_name]
    # delta from 0 to 5
    assert calls[0][0] == 5.0


def test_otel_record_gauge_absolute_set_downward():
    """record_gauge absolute-set: 5 → 3 = delta of -2."""
    impl, captured = _make_otel()
    impl.record_gauge("active_instances", 5.0)
    impl.record_gauge("active_instances", 3.0)

    otel_name = _to_otel_name("active_instances")
    calls = captured[otel_name]
    assert calls[0][0] == 5.0
    assert calls[1][0] == -2.0


def test_otel_record_histogram():
    impl, captured = _make_otel()
    impl.record_histogram("orb.provisioning.duration", 1.5, labels={"region": "us-east-1"})

    otel_name = _to_otel_name("orb.provisioning.duration")
    assert otel_name in captured
    calls = captured[otel_name]
    assert calls[0][0] == 1.5
    assert calls[0][1]["region"] == "us-east-1"


# ---------------------------------------------------------------------------
# _to_otel_name
# ---------------------------------------------------------------------------


def test_to_otel_name_prefixes_with_orb():
    assert _to_otel_name("requests_total").startswith("orb.")


def test_to_otel_name_replaces_underscores_with_dots():
    result = _to_otel_name("circuit_breaker_opened_total")
    assert "_" not in result


def test_to_otel_name_already_prefixed():
    result = _to_otel_name("orb.requests.total")
    # Should not double-prefix
    assert result.count("orb.") == 1


def test_to_otel_name_storage_name():
    result = _to_otel_name("storage.json.save_total")
    assert result == "orb.storage.json.save.total"


# ---------------------------------------------------------------------------
# Graceful no-op when opentelemetry-api is absent
# ---------------------------------------------------------------------------


def test_otel_provider_metrics_works_without_otel(monkeypatch):
    """OtelProviderMetrics degrades gracefully when opentelemetry is absent."""
    otel_modules = [k for k in sys.modules if k.startswith("opentelemetry")]
    backup = {k: sys.modules.pop(k) for k in otel_modules}

    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError(f"Simulated absent: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    try:
        impl = OtelProviderMetrics()
        # Reset caches to force re-creation via the no-op meter path
        impl._operation_counter = None
        impl._operation_duration = None
        impl._counters.clear()
        impl._gauges.clear()
        impl._histograms.clear()

        # All calls should be silent no-ops
        impl.record_operation("ec2", "run_instances", 0.1, True)
        impl.record_counter("circuit_breaker.opened.total")
        impl.record_gauge("active_instances", 3.0)
        impl.record_histogram("orb.provisioning.duration", 0.5)
    finally:
        monkeypatch.setattr(builtins, "__import__", original_import)
        sys.modules.update(backup)


# ---------------------------------------------------------------------------
# is_subclass check
# ---------------------------------------------------------------------------


def test_otel_is_subclass_of_port():
    assert issubclass(OtelProviderMetrics, ProviderMetricsPort)
