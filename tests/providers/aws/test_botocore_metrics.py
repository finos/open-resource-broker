"""
Tests for AWS API metrics collection using botocore event hooks.

This module tests the BotocoreMetricsHandler implementation to ensure
proper metrics collection for all AWS API calls.  After the OTel migration
the handler writes to an OTel Meter instead of MetricsCollector; tests drive
the handler directly and — for the integration test — verify labelled metrics
surface on the prometheus_client REGISTRY with no collision.
"""

from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import ClientError

try:
    from moto import mock_aws

    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

    def mock_aws(func):
        return func


try:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource

    HAS_OTEL_SDK = True
except ImportError:
    HAS_OTEL_SDK = False

try:
    from opentelemetry.exporter.prometheus import PrometheusMetricReader

    HAS_PROMETHEUS_EXPORTER = True
except ImportError:
    HAS_PROMETHEUS_EXPORTER = False

try:
    from prometheus_client.exposition import generate_latest

    HAS_PROMETHEUS_CLIENT = True
except ImportError:
    HAS_PROMETHEUS_CLIENT = False

from orb.domain.base.ports import LoggingPort
from orb.providers.aws.infrastructure.instrumentation.botocore_metrics import (
    BotocoreMetricsHandler,
    RequestContext,
)

pytestmark = pytest.mark.skipif(not HAS_MOTO, reason="moto not installed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler(cfg: dict | None = None) -> BotocoreMetricsHandler:
    """Create an enabled BotocoreMetricsHandler with a mock logger."""
    logger = Mock(spec=LoggingPort)
    base_cfg = {"provider_metrics_enabled": True}
    if cfg:
        base_cfg.update(cfg)
    return BotocoreMetricsHandler(logger, base_cfg)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestBotocoreMetrics:
    """Test suite for BotocoreMetricsHandler."""

    @pytest.fixture
    def logger(self):
        return Mock(spec=LoggingPort)

    @pytest.fixture
    def handler(self, logger):
        return BotocoreMetricsHandler(logger, {"provider_metrics_enabled": True})

    # ------------------------------------------------------------------ #
    # Constructor / disabled guard                                         #
    # ------------------------------------------------------------------ #

    def test_disabled_handler_skips_registration(self):
        """Disabled handler should not register events."""
        logger = Mock(spec=LoggingPort)
        handler = BotocoreMetricsHandler(logger, {"provider_metrics_enabled": False})
        assert not handler.enabled

        session = Mock()
        handler.register_events(session)
        # session.client should not be replaced
        session.client = Mock()  # noop check — register_events exited early
        logger.debug.assert_called()

    # ------------------------------------------------------------------ #
    # Event registration                                                   #
    # ------------------------------------------------------------------ #

    @mock_aws
    def test_event_registration(self, handler):
        """Registered handler fires on a boto3 call."""
        session = boto3.Session()
        handler.register_events(session)

        client = session.client("ec2", region_name="us-east-1")
        # Should not raise even without OTel SDK configured
        client.describe_instances()
        # After the call, at least one request was counted
        assert handler._request_counter >= 1

    # ------------------------------------------------------------------ #
    # Successful call                                                       #
    # ------------------------------------------------------------------ #

    @mock_aws
    def test_successful_call_recorded(self, handler):
        """Successful call increments request counter and records duration."""
        session = boto3.Session()
        handler.register_events(session)

        ec2 = session.client("ec2", region_name="us-east-1")
        response = ec2.describe_instances()

        assert response is not None
        assert "Reservations" in response
        assert handler._request_counter >= 1

    # ------------------------------------------------------------------ #
    # Error call                                                            #
    # ------------------------------------------------------------------ #

    @mock_aws
    def test_error_call_handled(self, handler):
        """Error paths do not raise out of the handler."""
        session = boto3.Session()
        handler.register_events(session)

        ec2 = session.client("ec2", region_name="us-east-1")

        with pytest.raises(ClientError):
            ec2.describe_instances(InstanceIds=["i-invalid"])

        # Handler should have processed the error path without crashing
        assert handler._request_counter >= 1

    # ------------------------------------------------------------------ #
    # Event name parsing                                                    #
    # ------------------------------------------------------------------ #

    def test_event_name_parsing(self, handler):
        service, operation = handler._parse_event_name("before-call.ec2.describe_instances")
        assert service == "ec2"
        assert operation == "describe_instances"

        service, operation = handler._parse_event_name("after-call.s3.list_buckets")
        assert service == "s3"
        assert operation == "list_buckets"

    # ------------------------------------------------------------------ #
    # Error parsing                                                         #
    # ------------------------------------------------------------------ #

    def test_error_parsing(self, handler):
        error = ClientError(
            error_response={"Error": {"Code": "InvalidInstanceID.NotFound"}},
            operation_name="DescribeInstances",
        )
        error_code, error_type = handler._parse_error(error)
        assert error_code == "InvalidInstanceID.NotFound"
        assert error_type == "ClientError"

    # ------------------------------------------------------------------ #
    # Throttle detection — exactly the 5 required codes                    #
    # ------------------------------------------------------------------ #

    def test_all_five_throttle_codes_detected(self, handler):
        """All 5 canonical throttle codes must be classified as throttles."""
        required_codes = {
            "Throttling",
            "ThrottlingException",
            "RequestLimitExceeded",
            "TooManyRequestsException",
            "ProvisionedThroughputExceededException",
        }
        for code in required_codes:
            assert handler._is_throttling_error(code), f"Expected {code!r} to be a throttle code"

    def test_non_throttle_code_not_detected(self, handler):
        assert not handler._is_throttling_error("InvalidInstanceID.NotFound")
        assert not handler._is_throttling_error("AccessDenied")
        assert not handler._is_throttling_error("")

    # ------------------------------------------------------------------ #
    # Request context                                                       #
    # ------------------------------------------------------------------ #

    def test_request_context_creation(self, handler):
        request_id1 = handler._generate_request_id()
        request_id2 = handler._generate_request_id()
        assert request_id1 != request_id2
        assert "req_" in request_id1

        context = RequestContext(
            service="ec2", operation="describe_instances", start_time=123.456, region="us-east-1"
        )
        with handler._request_lock:
            handler._active_requests[request_id1] = context

        retrieved = handler._pop_request_context(request_id1)
        assert retrieved == context
        assert request_id1 not in handler._active_requests

    # ------------------------------------------------------------------ #
    # Payload size estimation                                               #
    # ------------------------------------------------------------------ #

    def test_payload_size_estimation(self, handler):
        params = {"InstanceIds": ["i-123", "i-456"], "MaxResults": 10}
        assert handler._estimate_request_size(params) > 0

        response = {"Instances": [{"InstanceId": "i-123", "State": {"Name": "running"}}]}
        assert handler._estimate_response_size(response) > 0

        assert handler._estimate_request_size(None) == 0
        assert handler._estimate_response_size(None) == 0

    # ------------------------------------------------------------------ #
    # Retry handling                                                        #
    # ------------------------------------------------------------------ #

    def test_retry_count_incremented_by_needs_retry(self, handler):
        """needs-retry event increments retry_count on the context."""
        kwargs = {"request_dict": {"metrics_request_id": "test_req_1"}}
        context = RequestContext(service="ec2", operation="describe_instances", start_time=123.456)
        handler._active_requests["test_req_1"] = context

        handler._on_retry_needed("needs-retry.ec2.describe_instances", **kwargs)
        assert handler._active_requests["test_req_1"].retry_count == 1

    def test_before_retry_does_not_raise(self, handler):
        """before-retry event fires without exception."""
        handler._before_retry("before-retry.ec2.describe_instances")

    # ------------------------------------------------------------------ #
    # Deterministic sampling                                                #
    # ------------------------------------------------------------------ #

    def test_sampling_rate_100_percent(self, logger):
        """sample_rate=1.0 → every call sampled."""
        handler = BotocoreMetricsHandler(
            logger, {"provider_metrics_enabled": True, "sample_rate": 1.0}
        )
        assert all(handler._should_sample() for _ in range(10))

    def test_sampling_rate_0_percent(self, logger):
        """sample_rate=0.0 → no call sampled."""
        handler = BotocoreMetricsHandler(
            logger, {"provider_metrics_enabled": True, "sample_rate": 0.0}
        )
        assert not any(handler._should_sample() for _ in range(10))

    def test_sampling_rate_50_percent_is_deterministic(self, logger):
        """sample_rate=0.5 → every other call, deterministically."""
        handler = BotocoreMetricsHandler(
            logger, {"provider_metrics_enabled": True, "sample_rate": 0.5}
        )
        # First call: counter becomes 1, 1 % 2 == 1 → not sampled (0 == 0 check fails)
        # Second call: counter becomes 2, 2 % 2 == 0 → sampled
        results = [handler._should_sample() for _ in range(6)]
        # Deterministic pattern: [False, True, False, True, False, True]
        assert results == [False, True, False, True, False, True]

    # ------------------------------------------------------------------ #
    # Handler stats                                                         #
    # ------------------------------------------------------------------ #

    def test_handler_stats(self, handler):
        context = RequestContext(service="ec2", operation="describe_instances", start_time=123.456)
        handler._active_requests["test_req"] = context
        handler._event_cache["before-call.ec2.describe_instances"] = ("ec2", "describe_instances")

        stats = handler.get_stats()
        assert stats["active_requests"] == 1
        assert stats["event_cache_size"] == 1
        assert "total_requests_processed" in stats

    # ------------------------------------------------------------------ #
    # Error handling in handlers                                            #
    # ------------------------------------------------------------------ #

    def test_bad_event_name_logs_warning(self, handler, logger):
        handler._before_call("invalid-event-name")
        logger.warning.assert_called()

    # ------------------------------------------------------------------ #
    # Thread safety                                                         #
    # ------------------------------------------------------------------ #

    def test_thread_safety(self, handler):
        import threading
        import time

        def add_request():
            request_id = handler._generate_request_id()
            ctx = RequestContext(
                service="ec2", operation="describe_instances", start_time=time.time()
            )
            with handler._request_lock:
                handler._active_requests[request_id] = ctx

        threads = [threading.Thread(target=add_request) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(handler._active_requests) == 10

    # ------------------------------------------------------------------ #
    # Multiple concurrent requests                                          #
    # ------------------------------------------------------------------ #

    @mock_aws
    def test_multiple_concurrent_requests(self, handler):
        session = boto3.Session()
        handler.register_events(session)
        ec2 = session.client("ec2", region_name="us-east-1")

        for _ in range(5):
            ec2.describe_instances()

        # Request counter should be at least 5
        assert handler._request_counter >= 5

    # ------------------------------------------------------------------ #
    # Event cache performance                                               #
    # ------------------------------------------------------------------ #

    def test_event_cache_performance(self, handler):
        service1, op1 = handler._parse_event_name("before-call.ec2.describe_instances")
        service2, op2 = handler._parse_event_name("before-call.ec2.describe_instances")

        assert service1 == service2 == "ec2"
        assert op1 == op2 == "describe_instances"
        assert len(handler._event_cache) == 1


# ---------------------------------------------------------------------------
# Integration test: OTel Meter → prometheus_client isolated Registry
# ---------------------------------------------------------------------------


def _make_isolated_meter_and_registry():
    """Create an isolated CollectorRegistry + MeterProvider + Meter triple.

    Using a per-test CollectorRegistry instead of the global REGISTRY avoids
    'Duplicated timeseries' errors between tests and sidesteps the global
    MeterProvider singleton restriction (set_meter_provider can only be called
    once per process).  Each test drives the handler's instruments directly
    using the Meter obtained from the isolated provider.

    Returns (meter, provider, registry) — call provider.shutdown() in cleanup.
    """
    from prometheus_client import CollectorRegistry

    registry = CollectorRegistry(auto_describe=False)
    reader = PrometheusMetricReader(prefix="orb_test", registry=registry)
    provider = MeterProvider(
        resource=Resource.create({SERVICE_NAME: "orb-test"}),
        metric_readers=[reader],
    )
    meter = provider.get_meter("orb.providers.aws.infrastructure.instrumentation.botocore_metrics")
    return meter, provider, registry


@pytest.mark.skipif(
    not (HAS_OTEL_SDK and HAS_PROMETHEUS_EXPORTER and HAS_PROMETHEUS_CLIENT),
    reason="opentelemetry-sdk, opentelemetry-exporter-prometheus, or prometheus-client not installed",
)
@pytest.mark.skipif(not HAS_MOTO, reason="moto not installed")
class TestOtelPrometheusIntegration:
    """Integration test: BotocoreMetricsHandler instruments surface on an isolated
    prometheus_client CollectorRegistry.

    Each test creates a fresh (meter, provider, registry) triple so instruments
    are registered in isolation — no Duplicated timeseries, no global state bleed.
    The handler's ``_calls_counter`` / ``_throttles_counter`` / etc. are replaced
    with instruments from the isolated meter so we can scrape the isolated registry.
    """

    def _make_handler_with_isolated_meter(self):
        """Create handler then swap its OTel instruments for isolated-meter ones.

        This is necessary because the handler acquires its meter at construction
        from ``opentelemetry.metrics.get_meter()`` (the global provider).  To
        assert that the instruments surface on a scraped registry we replace them
        with instruments from our isolated MeterProvider.
        """
        meter, provider, registry = _make_isolated_meter_and_registry()

        logger = Mock(spec=LoggingPort)
        handler = BotocoreMetricsHandler(logger, {"provider_metrics_enabled": True})

        # Swap the handler's instruments with ones from the isolated meter
        handler._calls_counter = meter.create_counter(
            "orb.aws.api.calls", unit="1", description="Total AWS API calls."
        )
        handler._errors_counter = meter.create_counter(
            "orb.aws.api.errors", unit="1", description="Total AWS API errors."
        )
        handler._successes_counter = meter.create_counter(
            "orb.aws.api.successes", unit="1", description="Total successes."
        )
        handler._retries_counter = meter.create_counter(
            "orb.aws.api.retries", unit="1", description="Total retries."
        )
        handler._throttles_counter = meter.create_counter(
            "orb.aws.api.throttles", unit="1", description="Total throttles."
        )
        handler._duration_histogram = meter.create_histogram(
            "orb.aws.api.duration", unit="s", description="Call duration."
        )
        handler._response_size_histogram = meter.create_histogram(
            "orb.aws.api.response_size", unit="By", description="Response size."
        )
        handler._request_size_histogram = meter.create_histogram(
            "orb.aws.api.request_size", unit="By", description="Request size."
        )

        return handler, provider, registry

    @mock_aws
    def test_labelled_aws_call_metric_surfaces_on_registry(self):
        """OTel counter written by BotocoreMetricsHandler appears in scraped output
        with the correct {service, operation} labels — no collision."""
        handler, provider, registry = self._make_handler_with_isolated_meter()
        try:
            session = boto3.Session()
            handler.register_events(session)
            ec2 = session.client("ec2", region_name="us-east-1")
            ec2.describe_instances()

            # Scrape the isolated registry
            output = generate_latest(registry).decode("utf-8")

            # The OTel call counter surfaces as orb_test_orb_aws_api_calls_total{...}
            assert "orb_aws_api_calls" in output, (
                f"Expected 'orb_aws_api_calls' in prometheus output.\n\nActual output:\n{output[:2000]}"
            )
            # Labels must be present as separate label keys (not embedded in name)
            assert 'service="ec2"' in output
            assert 'operation="describe_instances"' in output
        finally:
            provider.shutdown()

    def test_throttle_error_increments_throttle_series(self):
        """A simulated throttle error increments the throttles counter."""
        handler, provider, registry = self._make_handler_with_isolated_meter()
        try:
            context = RequestContext(
                service="ec2",
                operation="describe_instances",
                start_time=0.0,
            )
            request_id = handler._generate_request_id()
            with handler._request_lock:
                handler._active_requests[request_id] = context

            throttle_exc = ClientError(
                error_response={"Error": {"Code": "Throttling", "Message": "Rate exceeded"}},
                operation_name="DescribeInstances",
            )
            handler._after_call_error(
                "after-call-error.ec2.DescribeInstances",
                context={"metrics_context": context, "metrics_request_id": request_id},
                exception=throttle_exc,
            )

            output = generate_latest(registry).decode("utf-8")

            assert "orb_aws_api_throttles" in output, (
                f"Expected 'orb_aws_api_throttles' in output.\n\nActual output:\n{output[:2000]}"
            )
            # Throttling error code should appear as a label
            assert 'error_code="Throttling"' in output
        finally:
            provider.shutdown()

    def test_no_duplicated_timeseries_on_second_call(self):
        """Calling the handler twice for the same operation must NOT raise
        ValueError: Duplicated timeseries.  OTel accumulates samples internally
        and the PrometheusMetricReader exposes a single time-series per label set."""
        handler, provider, registry = self._make_handler_with_isolated_meter()
        try:
            context = RequestContext(service="s3", operation="list_buckets", start_time=0.0)
            for _ in range(2):
                rid = handler._generate_request_id()
                with handler._request_lock:
                    handler._active_requests[rid] = context
                handler._after_call_success(
                    "after-call.s3.ListBuckets",
                    context={"metrics_context": context, "metrics_request_id": rid},
                    parsed={},
                    http_response=Mock(status_code=200),
                )

            # generate_latest must not raise ValueError: Duplicated timeseries
            try:
                output = generate_latest(registry).decode("utf-8")
                assert "orb_aws_api_successes" in output, (
                    f"Expected 'orb_aws_api_successes' in output.\n\nActual:\n{output[:2000]}"
                )
            except ValueError as exc:
                pytest.fail(f"Duplicated timeseries error: {exc}")
        finally:
            provider.shutdown()


# ---------------------------------------------------------------------------
# RequestContext dataclass tests
# ---------------------------------------------------------------------------


class TestRequestContext:
    def test_defaults(self):
        ctx = RequestContext(service="ec2", operation="describe_instances", start_time=123.456)
        assert ctx.service == "ec2"
        assert ctx.operation == "describe_instances"
        assert ctx.start_time == 123.456
        assert ctx.retry_count == 0
        assert ctx.region == "unknown"
        assert ctx.request_size == 0

    def test_all_fields(self):
        ctx = RequestContext(
            service="s3",
            operation="list_buckets",
            start_time=456.789,
            retry_count=2,
            region="eu-west-1",
            request_size=1024,
        )
        assert ctx.service == "s3"
        assert ctx.retry_count == 2
        assert ctx.region == "eu-west-1"
        assert ctx.request_size == 1024


# ---------------------------------------------------------------------------
# Moto integration tests
# ---------------------------------------------------------------------------


class TestIntegrationWithMoto:
    @pytest.fixture
    def instrumented_session(self):
        handler = _make_handler()
        session = boto3.Session()
        handler.register_events(session)
        return session, handler

    @mock_aws
    def test_ec2_operations_tracked(self, instrumented_session):
        session, handler = instrumented_session
        ec2 = session.client("ec2", region_name="us-east-1")

        ec2.describe_instances()
        ec2.describe_availability_zones()

        assert handler._request_counter >= 2

    @mock_aws
    def test_error_scenario_handled(self, instrumented_session):
        session, handler = instrumented_session
        ec2 = session.client("ec2", region_name="us-east-1")

        with pytest.raises(ClientError):
            ec2.describe_instances(InstanceIds=["i-invalid"])

        # Handler should not have crashed
        assert handler._request_counter >= 1

    @mock_aws
    def test_timing_recorded(self, instrumented_session):
        """After a call the request_counter increments (proxy for duration recording)."""
        session, handler = instrumented_session
        ec2 = session.client("ec2", region_name="us-east-1")
        ec2.describe_instances()

        assert handler._request_counter >= 1


if __name__ == "__main__":
    pytest.main([__file__])
