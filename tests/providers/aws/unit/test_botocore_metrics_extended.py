"""Extended unit tests for BotocoreMetricsHandler — covers uncovered branches."""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from orb.providers.aws.infrastructure.instrumentation.botocore_metrics import (
    BotocoreMetricsHandler,
    RequestContext,
    _NoOpCounter,
    _NoOpHistogram,
    _NoOpMeter,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler(
    enabled: bool = True, sample_rate: float = 1.0, **cfg_extras
) -> BotocoreMetricsHandler:
    logger = MagicMock()
    cfg = {
        "provider_metrics_enabled": enabled,
        "sample_rate": sample_rate,
        **cfg_extras,
    }
    return BotocoreMetricsHandler(logger=logger, aws_metrics_config=cfg)


# ---------------------------------------------------------------------------
# No-op stubs
# ---------------------------------------------------------------------------


class TestNoOpStubs:
    def test_no_op_counter_add_does_not_raise(self):
        c = _NoOpCounter()
        c.add(1, {"x": "y"})

    def test_no_op_histogram_record_does_not_raise(self):
        h = _NoOpHistogram()
        h.record(0.5, {"x": "y"})

    def test_no_op_meter_creates_counter(self):
        m = _NoOpMeter()
        c = m.create_counter("some_counter")
        assert isinstance(c, _NoOpCounter)

    def test_no_op_meter_creates_histogram(self):
        m = _NoOpMeter()
        h = m.create_histogram("some_histogram")
        assert isinstance(h, _NoOpHistogram)


# ---------------------------------------------------------------------------
# Disabled metrics — fast guards
# ---------------------------------------------------------------------------


class TestDisabledMetrics:
    def test_disabled_handler_has_enabled_false(self):
        h = _make_handler(enabled=False)
        assert not h.enabled

    def test_register_events_noop_when_disabled(self):
        h = _make_handler(enabled=False)
        session = MagicMock()
        h.register_events(session)
        # Should NOT wrap session.client
        session.client.assert_not_called()

    def test_before_call_returns_early_when_disabled(self):
        h = _make_handler(enabled=False)
        # A disabled handler must record no metrics whatsoever.
        h._calls_counter = MagicMock()
        h._request_size_histogram = MagicMock()
        h._before_call("before-call.ec2.DescribeInstances")
        h._calls_counter.add.assert_not_called()
        h._request_size_histogram.record.assert_not_called()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestParseEventName:
    def test_valid_before_call_event(self):
        h = _make_handler()
        svc, op = h._parse_event_name("before-call.ec2.DescribeInstances")
        assert svc == "ec2"
        assert op == "describe_instances"

    def test_valid_after_call_event(self):
        h = _make_handler()
        svc, op = h._parse_event_name("after-call.s3.GetObject")
        assert svc == "s3"
        assert op == "get_object"

    def test_unknown_event_name_returns_unknown(self):
        h = _make_handler()
        svc, op = h._parse_event_name("something_invalid")
        assert svc == "unknown"
        assert op == "unknown"

    def test_fallback_parsing_three_parts(self):
        h = _make_handler()
        svc, op = h._parse_event_name("x.myservice.GetSomething")
        assert svc == "myservice"
        assert op == "get_something"

    def test_caching_returns_same_result(self):
        h = _make_handler()
        result1 = h._parse_event_name("before-call.ec2.RunInstances")
        result2 = h._parse_event_name("before-call.ec2.RunInstances")
        assert result1 == result2


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalizeOperationName:
    def test_camel_to_snake(self):
        h = _make_handler()
        assert h._normalize_operation_name("DescribeInstances") == "describe_instances"

    def test_single_word(self):
        h = _make_handler()
        assert h._normalize_operation_name("GetObject") == "get_object"

    def test_already_lower(self):
        h = _make_handler()
        assert h._normalize_operation_name("list") == "list"


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


class TestShouldSample:
    def test_sample_rate_one_always_samples(self):
        h = _make_handler(sample_rate=1.0)
        for _ in range(10):
            assert h._should_sample()

    def test_sample_rate_zero_never_samples(self):
        h = _make_handler(sample_rate=0.0)
        for _ in range(10):
            assert not h._should_sample()

    def test_sample_rate_half_is_deterministic(self):
        h = _make_handler(sample_rate=0.5)
        results = [h._should_sample() for _ in range(10)]
        # every other call (modulo 2 == 0) → expect exactly 5 True
        true_count = sum(results)
        assert true_count == 5


# ---------------------------------------------------------------------------
# Error parsing
# ---------------------------------------------------------------------------


class TestParseError:
    def test_client_error_extracts_code(self):
        h = _make_handler()
        err = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "op")
        code, etype = h._parse_error(err)
        assert code == "AccessDenied"
        assert etype == "ClientError"

    def test_generic_exception_returns_unknown_code(self):
        h = _make_handler()
        code, etype = h._parse_error(ValueError("bad"))
        assert code == "Unknown"
        assert etype == "ValueError"

    def test_none_exception_returns_unknown(self):
        h = _make_handler()
        code, etype = h._parse_error(None)  # type: ignore[arg-type]
        assert code == "Unknown"
        assert etype == "Unknown"


# ---------------------------------------------------------------------------
# Throttling detection
# ---------------------------------------------------------------------------


class TestIsThrottlingError:
    def test_throttling_code_is_throttling(self):
        h = _make_handler()
        assert h._is_throttling_error("Throttling")

    def test_throttling_exception_code(self):
        h = _make_handler()
        assert h._is_throttling_error("ThrottlingException")

    def test_request_limit_exceeded(self):
        h = _make_handler()
        assert h._is_throttling_error("RequestLimitExceeded")

    def test_too_many_requests_exception(self):
        h = _make_handler()
        assert h._is_throttling_error("TooManyRequestsException")

    def test_provisioned_throughput_exceeded(self):
        h = _make_handler()
        assert h._is_throttling_error("ProvisionedThroughputExceededException")

    def test_non_throttling_code(self):
        h = _make_handler()
        assert not h._is_throttling_error("AccessDenied")


# ---------------------------------------------------------------------------
# Payload size estimation
# ---------------------------------------------------------------------------


class TestEstimateSizes:
    def test_empty_request_returns_zero(self):
        h = _make_handler()
        assert h._estimate_request_size({}) == 0

    def test_non_empty_request_returns_positive(self):
        h = _make_handler()
        size = h._estimate_request_size({"key": "value"})
        assert size > 0

    def test_empty_response_returns_zero(self):
        h = _make_handler()
        assert h._estimate_response_size({}) == 0

    def test_non_empty_response_returns_positive(self):
        h = _make_handler()
        size = h._estimate_response_size({"Instances": [{"InstanceId": "i-001"}]})
        assert size > 0


# ---------------------------------------------------------------------------
# Request ID generation and extraction
# ---------------------------------------------------------------------------


class TestRequestIdGeneration:
    def test_generate_request_id_is_unique(self):
        h = _make_handler()
        ids = {h._generate_request_id() for _ in range(10)}
        assert len(ids) == 10

    def test_extract_request_id_from_request_dict(self):
        h = _make_handler()
        rid = h._extract_request_id({"request_dict": {"metrics_request_id": "req_123"}})
        assert rid == "req_123"

    def test_extract_request_id_from_context(self):
        h = _make_handler()
        rid = h._extract_request_id({"context": {"metrics_request_id": "req_456"}})
        assert rid == "req_456"

    def test_extract_request_id_returns_none_when_absent(self):
        h = _make_handler()
        rid = h._extract_request_id({})
        assert rid is None

    def test_pop_request_context_returns_none_when_absent(self):
        h = _make_handler()
        assert h._pop_request_context("nonexistent") is None


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_stats_returns_expected_keys(self):
        h = _make_handler()
        stats = h.get_stats()
        assert "active_requests" in stats
        assert "event_cache_size" in stats
        assert "total_requests_processed" in stats

    def test_initial_stats_are_zero(self):
        h = _make_handler()
        stats = h.get_stats()
        assert stats["active_requests"] == 0
        assert stats["total_requests_processed"] == 0


# ---------------------------------------------------------------------------
# _before_call event handling
# ---------------------------------------------------------------------------


class TestBeforeCallHandler:
    def test_records_call_counter(self):
        h = _make_handler()
        h._calls_counter = MagicMock()
        h._before_call("before-call.ec2.DescribeInstances", context={})
        h._calls_counter.add.assert_called_once()

    def test_skips_when_service_not_in_monitored_services(self):
        h = _make_handler(monitored_services=["s3"])
        h._calls_counter = MagicMock()
        h._before_call("before-call.ec2.DescribeInstances", context={})
        h._calls_counter.add.assert_not_called()

    def test_skips_when_operation_not_in_monitored_operations(self):
        h = _make_handler(monitored_operations=["get_object"])
        h._calls_counter = MagicMock()
        h._before_call("before-call.ec2.DescribeInstances", context={})
        h._calls_counter.add.assert_not_called()

    def test_propagates_request_id_to_request_dict(self):
        h = _make_handler()
        request_dict: dict = {}
        h._before_call(
            "before-call.ec2.DescribeInstances",
            context={},
            request_dict=request_dict,
        )
        assert "metrics_request_id" in request_dict

    def test_handles_exception_gracefully(self):
        h = _make_handler()
        h._calls_counter = MagicMock()
        h._calls_counter.add.side_effect = RuntimeError("counter blow up")
        # Should not propagate the exception, and must log it
        h._before_call("before-call.ec2.DescribeInstances", context={})
        h.logger.warning.assert_called_once()
        assert "counter blow up" in str(h.logger.warning.call_args)


# ---------------------------------------------------------------------------
# _after_call_success event handling
# ---------------------------------------------------------------------------


class TestAfterCallSuccessHandler:
    def _setup_context(self, handler: BotocoreMetricsHandler) -> RequestContext:
        ctx = RequestContext(
            service="ec2",
            operation="describe_instances",
            start_time=0.0,
        )
        with handler._request_lock:
            req_id = handler._generate_request_id()
            handler._active_requests[req_id] = ctx
        return ctx

    def test_records_success_counter(self):
        h = _make_handler()
        ctx = RequestContext(service="ec2", operation="describe_instances", start_time=0.0)
        h._successes_counter = MagicMock()
        h._duration_histogram = MagicMock()
        h._after_call_success(
            "after-call.ec2.DescribeInstances",
            context={"metrics_context": ctx},
            parsed={},
        )
        h._successes_counter.add.assert_called_once()

    def test_records_error_counter_on_4xx_status(self):
        h = _make_handler()
        ctx = RequestContext(service="ec2", operation="describe_instances", start_time=0.0)
        h._errors_counter = MagicMock()
        h._duration_histogram = MagicMock()
        http_response = MagicMock()
        http_response.status_code = 400
        h._after_call_success(
            "after-call.ec2.DescribeInstances",
            context={"metrics_context": ctx},
            parsed={},
            http_response=http_response,
        )
        h._errors_counter.add.assert_called_once()

    def test_records_retries_counter_when_retry_count_nonzero(self):
        h = _make_handler()
        ctx = RequestContext(
            service="ec2", operation="describe_instances", start_time=0.0, retry_count=3
        )
        h._retries_counter = MagicMock()
        h._successes_counter = MagicMock()
        h._duration_histogram = MagicMock()
        h._after_call_success(
            "after-call.ec2.DescribeInstances",
            context={"metrics_context": ctx},
            parsed={},
        )
        h._retries_counter.add.assert_called_once_with(
            3, {"service": "ec2", "operation": "describe_instances"}
        )

    def test_falls_back_to_active_requests_map(self):
        h = _make_handler()
        ctx = RequestContext(service="ec2", operation="describe_instances", start_time=0.0)
        with h._request_lock:
            h._active_requests["req_solo"] = ctx
        h._successes_counter = MagicMock()
        h._duration_histogram = MagicMock()
        # No context kwarg supplied — should pop from active_requests (single entry)
        h._after_call_success("after-call.ec2.DescribeInstances", parsed={})
        h._successes_counter.add.assert_called_once()

    def test_handles_exception_gracefully(self):
        h = _make_handler()
        h._duration_histogram = MagicMock()
        h._successes_counter = MagicMock()
        h._successes_counter.add.side_effect = RuntimeError("blow up")
        ctx = RequestContext(service="ec2", operation="describe_instances", start_time=0.0)
        # Should not raise, and must log it
        h._after_call_success(
            "after-call.ec2.DescribeInstances",
            context={"metrics_context": ctx},
            parsed={},
        )
        h.logger.warning.assert_called_once()
        assert "blow up" in str(h.logger.warning.call_args)


# ---------------------------------------------------------------------------
# _after_call_error event handling
# ---------------------------------------------------------------------------


class TestAfterCallErrorHandler:
    def test_records_error_counter(self):
        h = _make_handler()
        ctx = RequestContext(service="ec2", operation="describe_instances", start_time=0.0)
        h._errors_counter = MagicMock()
        h._duration_histogram = MagicMock()
        h._throttles_counter = MagicMock()
        err = ValueError("something went wrong")
        h._after_call_error(
            "after-call-error.ec2.DescribeInstances",
            context={"metrics_context": ctx},
            exception=err,
        )
        h._errors_counter.add.assert_called_once()

    def test_records_throttle_counter_for_throttling_error(self):
        h = _make_handler()
        ctx = RequestContext(service="ec2", operation="describe_instances", start_time=0.0)
        h._errors_counter = MagicMock()
        h._duration_histogram = MagicMock()
        h._throttles_counter = MagicMock()
        err = ClientError({"Error": {"Code": "Throttling", "Message": "too many"}}, "op")
        h._after_call_error(
            "after-call-error.ec2.DescribeInstances",
            context={"metrics_context": ctx},
            exception=err,
        )
        h._throttles_counter.add.assert_called_once()

    def test_handles_exception_gracefully(self):
        h = _make_handler()
        h._duration_histogram = MagicMock()
        h._errors_counter = MagicMock()
        h._errors_counter.add.side_effect = RuntimeError("blow up")
        ctx = RequestContext(service="ec2", operation="describe_instances", start_time=0.0)
        # Should not raise, and must log it
        h._after_call_error(
            "after-call-error.ec2.DescribeInstances",
            context={"metrics_context": ctx},
            exception=ValueError("original"),
        )
        h.logger.warning.assert_called_once()
        assert "blow up" in str(h.logger.warning.call_args)


# ---------------------------------------------------------------------------
# _on_retry_needed event handling
# ---------------------------------------------------------------------------


class TestOnRetryNeeded:
    def test_increments_retry_count(self):
        h = _make_handler()
        ctx = RequestContext(service="ec2", operation="describe_instances", start_time=0.0)
        h._after_call_success(
            "after-call.ec2.DescribeInstances",
            context={"metrics_context": ctx},
            parsed={},
        )
        # Now test retry increment
        ctx2 = RequestContext(service="ec2", operation="describe_instances", start_time=0.0)
        h._on_retry_needed(
            "needs-retry.ec2.DescribeInstances",
            request_context={"metrics_context": ctx2},
        )
        assert ctx2.retry_count == 1

    def test_handles_exception_gracefully(self):
        h = _make_handler()
        # Force the internal lookup to raise so the try/except is exercised
        h._extract_request_id = MagicMock(side_effect=RuntimeError("lookup boom"))
        # Should not raise, and must log it
        h._on_retry_needed("needs-retry.ec2.DescribeInstances", request_context={})
        h.logger.warning.assert_called_once()
        assert "lookup boom" in str(h.logger.warning.call_args)


# ---------------------------------------------------------------------------
# _before_retry event handling
# ---------------------------------------------------------------------------


class TestBeforeRetry:
    def test_records_retry_counter(self):
        h = _make_handler()
        h._retries_counter = MagicMock()
        h._before_retry("before-retry.ec2.DescribeInstances")
        h._retries_counter.add.assert_called_once_with(
            1, {"service": "ec2", "operation": "describe_instances"}
        )

    def test_handles_exception_gracefully(self):
        h = _make_handler()
        h._retries_counter = MagicMock()
        h._retries_counter.add.side_effect = RuntimeError("boom")
        # Should not raise, and must log it
        h._before_retry("before-retry.ec2.DescribeInstances")
        h.logger.warning.assert_called_once()
        assert "boom" in str(h.logger.warning.call_args)


# ---------------------------------------------------------------------------
# register_events
# ---------------------------------------------------------------------------


class TestRegisterEvents:
    def test_wraps_session_client_when_enabled(self):
        h = _make_handler(enabled=True)
        session = MagicMock()
        original_client = MagicMock(return_value=MagicMock())
        session.client = original_client

        h.register_events(session)

        # session.client should now be the wrapped version
        assert session.client is not original_client

    def test_wrapped_client_registers_events(self):
        h = _make_handler(enabled=True)
        session = MagicMock()
        mock_boto_client = MagicMock()
        mock_boto_client.meta.events = MagicMock()
        original_client = MagicMock(return_value=mock_boto_client)
        session.client = original_client

        h.register_events(session)

        # Call the wrapped client
        client = session.client("ec2")
        assert client is mock_boto_client
        mock_boto_client.meta.events.register.assert_called()


# ---------------------------------------------------------------------------
# Track payload sizes
# ---------------------------------------------------------------------------


class TestTrackPayloadSizes:
    def test_request_size_histogram_called_when_tracking_enabled(self):
        h = _make_handler(track_payload_sizes=True)
        h._calls_counter = MagicMock()
        h._request_size_histogram = MagicMock()

        h._before_call(
            "before-call.ec2.DescribeInstances",
            context={},
            params={"InstanceIds": ["i-001"]},
        )

        h._request_size_histogram.record.assert_called_once()

    def test_response_size_histogram_called_when_tracking_enabled(self):
        h = _make_handler(track_payload_sizes=True)
        ctx = RequestContext(service="ec2", operation="describe_instances", start_time=0.0)
        h._successes_counter = MagicMock()
        h._duration_histogram = MagicMock()
        h._response_size_histogram = MagicMock()

        h._after_call_success(
            "after-call.ec2.DescribeInstances",
            context={"metrics_context": ctx},
            parsed={"Instances": [{"InstanceId": "i-001"}]},
        )

        h._response_size_histogram.record.assert_called_once()
