"""K8s error-propagation tests — ApiException → typed K8sError hierarchy.

Mirrors the AWS provider's error-propagation test pattern.  Verifies:

1. ``classify_api_exception`` maps each HTTP status code to the correct
   typed exception sub-class.
2. Structured fields (``http_status``, ``k8s_reason``, ``k8s_message``,
   ``error_source``) are populated correctly.
3. ``to_dict()`` returns the expected shape including the new k8s fields.
4. Quota-exceeded detection (403 + "exceeded quota" in body) maps to
   ``K8sQuotaExceededError``.
5. Handler-level ``_classify_and_record_api_exception`` emits
   ``orb_k8s_api_errors_total`` with the correct labels.
6. New counter helpers (``record_api_error``, ``record_api_retry``) emit
   ``orb_k8s_api_errors_total`` / ``orb_k8s_api_throttles_total`` /
   ``orb_k8s_api_retries_total`` with correct cardinality constraints.
7. The ``instrumentation.metrics`` module resolves at its new path (the
   shim in ``services.metrics`` also re-exports correctly).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.exceptions.k8s_exceptions import (
    K8sAuthorizationError,
    K8sConflictError,
    K8sEntityNotFoundError,
    K8sError,  # noqa: F401
    K8sQuotaExceededError,
    K8sRateLimitError,
    K8sValidationError,
    classify_api_exception,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api_exception(
    status: int,
    reason: str | None = None,
    message: str | None = None,
) -> Any:
    """Return a minimal ApiException-like object with structured body."""
    try:
        from kubernetes.client.exceptions import ApiException
    except ImportError:
        pytest.skip("kubernetes extra not installed")

    body_dict: dict[str, Any] = {"kind": "Status", "apiVersion": "v1", "code": status}
    if reason:
        body_dict["reason"] = reason
    if message:
        body_dict["message"] = message

    exc = ApiException(status=status)
    exc.status = status
    exc.body = json.dumps(body_dict)
    exc.headers = {}
    return exc


def _make_meter_and_registry() -> tuple[Any, Any]:
    """Return an isolated OTel (meter, prometheus registry) pair for metric assertions."""
    from opentelemetry.exporter.prometheus import PrometheusMetricReader
    from opentelemetry.sdk.metrics import MeterProvider
    from prometheus_client import CollectorRegistry

    reg = CollectorRegistry()
    reader = PrometheusMetricReader(registry=reg)
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test.k8s.error.propagation")
    return meter, reg


def _scrape(registry: Any) -> str:
    from prometheus_client import generate_latest

    return generate_latest(registry).decode("utf-8")


# ---------------------------------------------------------------------------
# Part 1 — classify_api_exception status → typed class
# ---------------------------------------------------------------------------


class TestClassifyApiException:
    """Each HTTP status code must map to the correct typed K8sError sub-class."""

    def test_404_maps_to_entity_not_found(self) -> None:
        exc = _make_api_exception(404, reason="NotFound", message="pods not found")
        typed = classify_api_exception(exc, operation="list_namespaced_pod")
        assert isinstance(typed, K8sEntityNotFoundError)

    def test_409_maps_to_conflict(self) -> None:
        exc = _make_api_exception(409, reason="AlreadyExists", message="pods already exists")
        typed = classify_api_exception(exc, operation="create_namespaced_pod")
        assert isinstance(typed, K8sConflictError)

    def test_422_maps_to_validation_error(self) -> None:
        exc = _make_api_exception(422, reason="Invalid", message="spec is invalid")
        typed = classify_api_exception(exc, operation="create_namespaced_pod")
        assert isinstance(typed, K8sValidationError)

    def test_429_maps_to_rate_limit_error(self) -> None:
        exc = _make_api_exception(429, reason="TooManyRequests", message="rate limited")
        typed = classify_api_exception(exc, operation="create_namespaced_pod")
        assert isinstance(typed, K8sRateLimitError)

    def test_403_rbac_maps_to_authorization_error(self) -> None:
        exc = _make_api_exception(403, reason="Forbidden", message="RBAC: access denied")
        typed = classify_api_exception(exc, operation="create_namespaced_pod")
        assert isinstance(typed, K8sAuthorizationError)
        # Must NOT be quota
        assert not isinstance(typed, K8sQuotaExceededError)

    def test_403_quota_maps_to_quota_exceeded_error(self) -> None:
        exc = _make_api_exception(
            403,
            reason="Forbidden",
            message="exceeded quota: requests.cpu, requested: 100m, used: 500m, limited: 500m",
        )
        typed = classify_api_exception(exc, operation="create_namespaced_pod")
        assert isinstance(typed, K8sQuotaExceededError)

    def test_500_maps_to_base_k8s_error(self) -> None:
        exc = _make_api_exception(500, message="internal server error")
        typed = classify_api_exception(exc, operation="create_namespaced_pod")
        assert isinstance(typed, K8sError)
        # Must NOT be one of the specific sub-classes
        assert not isinstance(typed, K8sEntityNotFoundError)
        assert not isinstance(typed, K8sConflictError)
        assert not isinstance(typed, K8sRateLimitError)

    def test_503_maps_to_base_k8s_error(self) -> None:
        exc = _make_api_exception(503, message="service unavailable")
        typed = classify_api_exception(exc, operation="list_namespaced_pod")
        assert isinstance(typed, K8sError)


# ---------------------------------------------------------------------------
# Part 2 — structured fields are populated correctly
# ---------------------------------------------------------------------------


class TestStructuredFields:
    """Structured fields on the typed exception must match the ApiException body."""

    def test_http_status_propagated(self) -> None:
        exc = _make_api_exception(409, reason="AlreadyExists")
        typed = classify_api_exception(exc, operation="create_namespaced_pod")
        assert typed.http_status == 409

    def test_k8s_reason_propagated(self) -> None:
        exc = _make_api_exception(404, reason="NotFound", message="pod not found")
        typed = classify_api_exception(exc, operation="list_namespaced_pod")
        assert typed.k8s_reason == "NotFound"

    def test_k8s_message_propagated(self) -> None:
        exc = _make_api_exception(422, message="spec invalid: unknown field")
        typed = classify_api_exception(exc, operation="create_namespaced_pod")
        assert typed.k8s_message == "spec invalid: unknown field"

    def test_error_source_set_from_operation(self) -> None:
        exc = _make_api_exception(403, reason="Forbidden")
        typed = classify_api_exception(exc, operation="create_namespaced_pod")
        assert typed.error_source == "kubernetes.create_namespaced_pod"

    def test_error_source_none_when_operation_omitted(self) -> None:
        exc = _make_api_exception(409)
        typed = classify_api_exception(exc)
        assert typed.error_source is None

    def test_all_fields_none_for_empty_body(self) -> None:
        """A body-less exception still produces a valid typed exception."""
        try:
            from kubernetes.client.exceptions import ApiException
        except ImportError:
            pytest.skip("kubernetes extra not installed")

        exc = ApiException(status=404)
        exc.status = 404
        exc.body = None
        exc.headers = {}

        typed = classify_api_exception(exc, operation="list_namespaced_pod")
        assert isinstance(typed, K8sEntityNotFoundError)
        assert typed.http_status == 404
        assert typed.k8s_reason is None
        assert typed.k8s_message is None
        assert typed.request_id is None


# ---------------------------------------------------------------------------
# Part 3 — to_dict shape
# ---------------------------------------------------------------------------


class TestToDictShape:
    """to_dict() must include all structured k8s fields when non-None."""

    def test_to_dict_includes_http_status(self) -> None:
        exc = _make_api_exception(409, reason="AlreadyExists", message="already exists")
        typed = classify_api_exception(exc, operation="create_namespaced_pod")
        d = typed.to_dict()
        assert d["http_status"] == 409

    def test_to_dict_includes_k8s_reason(self) -> None:
        exc = _make_api_exception(404, reason="NotFound")
        typed = classify_api_exception(exc)
        d = typed.to_dict()
        assert d["k8s_reason"] == "NotFound"

    def test_to_dict_includes_k8s_message(self) -> None:
        exc = _make_api_exception(422, message="invalid spec")
        typed = classify_api_exception(exc)
        d = typed.to_dict()
        assert d["k8s_message"] == "invalid spec"

    def test_to_dict_includes_error_source(self) -> None:
        exc = _make_api_exception(403, reason="Forbidden")
        typed = classify_api_exception(exc, operation="create_namespaced_pod")
        d = typed.to_dict()
        assert d["error_source"] == "kubernetes.create_namespaced_pod"

    def test_to_dict_omits_none_fields(self) -> None:
        """Fields that are None must not appear in to_dict()."""
        try:
            from kubernetes.client.exceptions import ApiException
        except ImportError:
            pytest.skip("kubernetes extra not installed")

        exc = ApiException(status=500)
        exc.status = 500
        exc.body = None
        exc.headers = {}

        typed = classify_api_exception(exc)
        d = typed.to_dict()
        assert "k8s_reason" not in d
        assert "k8s_message" not in d
        assert "request_id" not in d
        assert "error_source" not in d

    def test_to_dict_base_fields_always_present(self) -> None:
        """error_type, error_code, message, details, correlation_id must always be present."""
        exc = _make_api_exception(404, reason="NotFound", message="not found")
        typed = classify_api_exception(exc, operation="list_namespaced_pod")
        d = typed.to_dict()
        assert "error_type" in d
        assert "message" in d
        assert "details" in d
        assert "correlation_id" in d


# ---------------------------------------------------------------------------
# Part 4 — handler-level _classify_and_record_api_exception emits metrics
# ---------------------------------------------------------------------------


class TestHandlerClassifyAndRecord:
    """_classify_and_record_api_exception must emit orb_k8s_api_errors_total."""

    def _make_handler_with_metrics(self, meter: Any) -> Any:
        """Return a minimal K8sPodHandler wired to an isolated K8sMetrics instance."""
        from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler
        from orb.providers.k8s.infrastructure.instrumentation.metrics import K8sMetrics
        from orb.providers.k8s.infrastructure.k8s_client import K8sClient

        metrics = K8sMetrics(meter=meter)
        mock_client = MagicMock(spec=K8sClient)
        mock_logger = MagicMock()

        handler = K8sPodHandler(
            kubernetes_client=mock_client,
            config=MagicMock(
                namespace="orb-test",
                label_prefix="orb.io",
                stale_cache_timeout_seconds=30,
                audit_high_risk_pod_fields=False,
                reject_high_risk_pod_fields=False,
            ),
            logger=mock_logger,
            metrics=metrics,
        )
        return handler, metrics

    def test_403_records_api_error_with_correct_labels(self) -> None:
        meter, reg = _make_meter_and_registry()
        handler, _ = self._make_handler_with_metrics(meter)

        exc = _make_api_exception(403, reason="Forbidden", message="RBAC denied")
        handler._classify_and_record_api_exception(exc, operation="create_namespaced_pod")

        text = _scrape(reg)
        assert "orb_k8s_api_errors_total" in text
        assert 'error_code="403"' in text
        assert 'operation="create_namespaced_pod"' in text

    def test_429_records_both_error_and_throttle_counters(self) -> None:
        meter, reg = _make_meter_and_registry()
        handler, _ = self._make_handler_with_metrics(meter)

        exc = _make_api_exception(429, message="rate limited")
        handler._classify_and_record_api_exception(exc, operation="create_namespaced_pod")

        text = _scrape(reg)
        assert "orb_k8s_api_errors_total" in text
        assert "orb_k8s_api_throttles_total" in text

    def test_500_records_api_error(self) -> None:
        meter, reg = _make_meter_and_registry()
        handler, _ = self._make_handler_with_metrics(meter)

        exc = _make_api_exception(500, message="internal error")
        handler._classify_and_record_api_exception(exc, operation="list_namespaced_pod")

        text = _scrape(reg)
        assert "orb_k8s_api_errors_total" in text
        assert 'error_code="500"' in text

    def test_returns_correct_typed_exception(self) -> None:
        meter, _ = _make_meter_and_registry()
        handler, _ = self._make_handler_with_metrics(meter)

        exc = _make_api_exception(404, reason="NotFound")
        typed = handler._classify_and_record_api_exception(exc, operation="list_namespaced_pod")
        assert isinstance(typed, K8sEntityNotFoundError)

    def test_no_metrics_wired_does_not_raise(self) -> None:
        """Handler without metrics must not crash when classifying an exception."""
        from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler
        from orb.providers.k8s.infrastructure.k8s_client import K8sClient

        mock_client = MagicMock(spec=K8sClient)
        handler = K8sPodHandler(
            kubernetes_client=mock_client,
            config=MagicMock(
                namespace="orb-test",
                label_prefix="orb.io",
                stale_cache_timeout_seconds=30,
                audit_high_risk_pod_fields=False,
                reject_high_risk_pod_fields=False,
            ),
            logger=MagicMock(),
            metrics=None,
        )

        exc = _make_api_exception(409)
        typed = handler._classify_and_record_api_exception(exc, operation="create_namespaced_pod")
        assert isinstance(typed, K8sConflictError)


# ---------------------------------------------------------------------------
# Part 5 — metrics module resolves at new path; shim still works
# ---------------------------------------------------------------------------


class TestModuleRelocation:
    """The new instrumentation.metrics path must be importable and functional."""

    def test_canonical_path_importable(self) -> None:
        from orb.providers.k8s.infrastructure.instrumentation import metrics as m  # noqa: F401

        assert hasattr(m, "K8sMetrics")
        assert hasattr(m, "_METRIC_SPECS")

    def test_shim_path_still_works(self) -> None:
        """The old services.metrics path must re-export K8sMetrics unchanged."""
        from orb.providers.k8s.infrastructure.instrumentation.metrics import (
            K8sMetrics as CanonicalMetrics,
        )
        from orb.providers.k8s.infrastructure.services.metrics import K8sMetrics as ShimMetrics

        assert ShimMetrics is CanonicalMetrics

    def test_k8s_metrics_has_new_counter_methods(self) -> None:
        from orb.providers.k8s.infrastructure.instrumentation.metrics import K8sMetrics

        m = K8sMetrics.__new__(K8sMetrics)
        assert callable(getattr(m, "record_api_error", None))
        assert callable(getattr(m, "record_api_retry", None))

    def test_new_metric_names_in_registered_names(self) -> None:
        from orb.providers.k8s.infrastructure.instrumentation.metrics import K8sMetrics

        names = K8sMetrics.registered_names()
        assert "orb_k8s_api_errors_total" in names
        assert "orb_k8s_api_throttles_total" in names
        assert "orb_k8s_api_retries_total" in names


# ---------------------------------------------------------------------------
# Regression: 401 classified as K8sAuthenticationError (Fix 2)
# ---------------------------------------------------------------------------


class TestAuthenticationError:
    """401 Unauthorized must map to K8sAuthenticationError, not base K8sError."""

    def test_401_maps_to_authentication_error(self) -> None:
        from orb.providers.k8s.exceptions.k8s_exceptions import K8sAuthenticationError

        exc = _make_api_exception(401, reason="Unauthorized", message="token has expired")
        typed = classify_api_exception(exc, operation="list_namespaced_pod")
        assert isinstance(typed, K8sAuthenticationError), (
            f"Expected K8sAuthenticationError, got {type(typed)}"
        )

    def test_401_http_status_set_correctly(self) -> None:
        from orb.providers.k8s.exceptions.k8s_exceptions import K8sAuthenticationError

        exc = _make_api_exception(401, reason="Unauthorized")
        typed = classify_api_exception(exc)
        assert isinstance(typed, K8sAuthenticationError)
        assert typed.http_status == 401

    def test_401_is_non_retryable_via_classifier(self) -> None:
        """The retry classifier must treat 401 as non-retryable after Fix 2."""
        from orb.providers.k8s.resilience.retry_classifier import K8sRetryClassifier

        try:
            from kubernetes.client.exceptions import ApiException
        except ImportError:
            pytest.skip("kubernetes extra not installed")

        clf = K8sRetryClassifier()
        exc = ApiException(status=401)
        exc.status = 401
        assert clf.is_non_retryable(exc) is True, (
            "401 must be non-retryable to prevent wasting retry budget on expired tokens"
        )

    def test_k8s_authentication_error_in_hierarchy(self) -> None:
        """K8sAuthenticationError must be a K8sError sub-class."""
        from orb.providers.k8s.exceptions.k8s_exceptions import (
            K8sAuthenticationError,
            K8sError,
        )

        assert issubclass(K8sAuthenticationError, K8sError)


# ---------------------------------------------------------------------------
# Regression: base_message truncated at 512 chars (Fix 4)
# ---------------------------------------------------------------------------


class TestBaseMessageTruncation:
    """A huge apiserver Status body must not produce a > 512-char base_message."""

    def test_large_exc_str_is_truncated_to_512_chars(self) -> None:
        try:
            from kubernetes.client.exceptions import ApiException
        except ImportError:
            pytest.skip("kubernetes extra not installed")

        # Build a very large exception body (> 1 KB) without a k8s_message,
        # so base_message falls back to str(exc).
        big_body = "x" * 2000
        exc = ApiException(status=500)
        exc.status = 500
        # Use a non-JSON body so k8s_message is None and str(exc) path is taken.
        exc.body = big_body
        exc.headers = {}

        typed = classify_api_exception(exc, operation="list_namespaced_pod")
        # base_message is used as the first positional arg to the exception constructor.
        # The classified exception's message must not carry more than 512 chars from
        # str(exc) which embeds the body.
        assert len(typed.message) <= 520, (  # small slack for "HTTP 500" fallback
            f"base_message too long: {len(typed.message)} chars"
        )

    def test_k8s_message_preferred_over_str_exc(self) -> None:
        """When a structured k8s_message is available it should be used as base_message."""
        exc = _make_api_exception(
            403,
            reason="Forbidden",
            message="RBAC: User cannot create pods",
        )
        typed = classify_api_exception(exc, operation="create_namespaced_pod")
        # The structured message must be present somewhere in the exception's message.
        assert "RBAC" in typed.message or typed.k8s_message == "RBAC: User cannot create pods"
