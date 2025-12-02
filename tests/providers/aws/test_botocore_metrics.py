"""
Tests for AWS API metrics collection using botocore event hooks.

This module tests the BotocoreMetricsHandler implementation to ensure
proper metrics collection for all AWS API calls.
"""

from unittest.mock import Mock

import boto3
import pytest
from moto import mock_aws

from domain.base.ports import LoggingPort
from monitoring.metrics import MetricsCollector
from providers.aws.infrastructure.instrumentation.botocore_metrics import (
    BotocoreMetricsHandler,
    RequestContext,
)


class TestBotocoreMetrics:
    """Test suite for BotocoreMetricsHandler."""

    @pytest.fixture
    def metrics_collector(self):
        """Mock metrics collector."""
        return Mock(spec=MetricsCollector)

    @pytest.fixture
    def logger(self):
        """Mock logger."""
        return Mock(spec=LoggingPort)

    @pytest.fixture
    def handler(self, metrics_collector, logger):
        """Create BotocoreMetricsHandler instance."""
        return BotocoreMetricsHandler(metrics_collector, logger)

    @mock_aws
    def test_event_registration(self, handler):
        """Test that event handlers are registered correctly."""
        session = boto3.Session()
        handler.register_events(session)

        # Verify handlers actually fire by performing a simple call
        client = session.client("ec2", region_name="us-east-1")
        client.describe_instances()
        handler.metrics.increment_counter.assert_called()

    @mock_aws
    def test_successful_call_metrics(self, handler, metrics_collector):
        """Test metrics collection for successful API calls."""
        session = boto3.Session()
        handler.register_events(session)

        ec2 = session.client("ec2", region_name="us-east-1")

        # Make a mocked API call
        response = ec2.describe_instances()

        # Verify metrics were recorded
        metrics_collector.increment_counter.assert_called()
        metrics_collector.record_time.assert_called()

        # Check call arguments
        call_args = metrics_collector.increment_counter.call_args_list
        assert any(call.args[0] == "aws.ec2.describe_instances.calls_total" for call in call_args)
        assert any(call.args[0] == "aws_api_calls_total" for call in call_args)

    @mock_aws
    def test_error_call_metrics(self, handler, metrics_collector):
        """Test metrics collection for failed API calls."""
        session = boto3.Session()
        handler.register_events(session)

        ec2 = session.client("ec2", region_name="us-east-1")

        # Make a call that will fail
        with pytest.raises(Exception):
            ec2.describe_instances(InstanceIds=["i-invalid"])

        # Verify error metrics were recorded
        call_args = metrics_collector.increment_counter.call_args_list
        assert any(call.args[0] == "aws.ec2.describe_instances.errors_total" for call in call_args)
        assert any(call.args[0] == "aws_api_errors_total" for call in call_args)

    def test_event_name_parsing(self, handler):
        """Test parsing of botocore event names."""
        service, operation = handler._parse_event_name("before-call.ec2.describe_instances")
        assert service == "ec2"
        assert operation == "describe_instances"

        service, operation = handler._parse_event_name("after-call.s3.list_buckets")
        assert service == "s3"
        assert operation == "list_buckets"

    def test_error_parsing(self, handler):
        """Test parsing of AWS errors."""
        from botocore.exceptions import ClientError

        error = ClientError(
            error_response={"Error": {"Code": "InvalidInstanceID.NotFound"}},
            operation_name="DescribeInstances",
        )

        error_code, error_type = handler._parse_error(error)
        assert error_code == "InvalidInstanceID.NotFound"
        assert error_type == "ClientError"

    def test_throttling_detection(self, handler):
        """Test detection of throttling errors."""
        assert handler._is_throttling_error("Throttling")
        assert handler._is_throttling_error("ThrottlingException")
        assert not handler._is_throttling_error("InvalidInstanceID.NotFound")

    def test_request_context_creation(self, handler):
        """Test RequestContext creation and management."""
        # Test request ID generation
        request_id1 = handler._generate_request_id()
        request_id2 = handler._generate_request_id()
        assert request_id1 != request_id2
        assert "req_" in request_id1

        # Test context storage and retrieval
        context = RequestContext(
            service="ec2", operation="describe_instances", start_time=123.456, region="us-east-1"
        )

        with handler._request_lock:
            handler._active_requests[request_id1] = context

        retrieved_context = handler._pop_request_context(request_id1)
        assert retrieved_context == context
        assert request_id1 not in handler._active_requests

    def test_payload_size_estimation(self, handler):
        """Test request and response payload size estimation."""
        # Test request size estimation
        params = {"InstanceIds": ["i-123", "i-456"], "MaxResults": 10}
        size = handler._estimate_request_size(params)
        assert size > 0

        # Test response size estimation
        response = {"Instances": [{"InstanceId": "i-123", "State": {"Name": "running"}}]}
        size = handler._estimate_response_size(response)
        assert size > 0

        # Test with invalid data
        assert handler._estimate_request_size(None) == 0
        assert handler._estimate_response_size(None) == 0

    def test_retry_handling(self, handler, metrics_collector):
        """Test retry event handling."""
        # Simulate retry needed event
        kwargs = {"request_dict": {"metrics_request_id": "test_req_1"}}

        # Add request context
        context = RequestContext(service="ec2", operation="describe_instances", start_time=123.456)
        handler._active_requests["test_req_1"] = context

        # Trigger retry needed event
        handler._on_retry_needed("needs-retry.ec2.describe_instances", **kwargs)

        # Check retry count was incremented
        assert handler._active_requests["test_req_1"].retry_count == 1

        # Trigger before retry event
        handler._before_retry("before-retry.ec2.describe_instances", **kwargs)

        # Verify retry metrics were recorded
        call_args = metrics_collector.increment_counter.call_args_list
        assert any("aws.ec2.describe_instances.retries_total" in str(call) for call in call_args)

    def test_handler_stats(self, handler):
        """Test handler statistics collection."""
        # Add some test data
        context = RequestContext(service="ec2", operation="describe_instances", start_time=123.456)
        handler._active_requests["test_req"] = context
        handler._event_cache["before-call.ec2.describe_instances"] = ("ec2", "describe_instances")

        stats = handler.get_stats()

        assert stats["active_requests"] == 1
        assert stats["event_cache_size"] == 1
        assert "total_requests_processed" in stats

    def test_error_handling_in_handlers(self, handler, logger):
        """Test error handling within event handlers."""
        # Test with invalid event name
        handler._before_call("invalid-event-name")

        # Should not crash and should log warning
        logger.warning.assert_called()

    @mock_aws
    def test_multiple_concurrent_requests(self, handler, metrics_collector):
        """Test handling multiple concurrent requests."""
        session = boto3.Session()
        handler.register_events(session)

        ec2 = session.client("ec2", region_name="us-east-1")

        # Make multiple concurrent calls
        for i in range(5):
            ec2.describe_instances()

        # Verify all calls were tracked
        call_count = len(
            [
                call
                for call in metrics_collector.increment_counter.call_args_list
                if call.args[0] == "aws.ec2.describe_instances.calls_total"
            ]
        )
        assert call_count == 5

    def test_event_cache_performance(self, handler):
        """Test event name caching for performance."""
        # First call should parse and cache
        service1, operation1 = handler._parse_event_name("before-call.ec2.describe_instances")

        # Second call should use cache
        service2, operation2 = handler._parse_event_name("before-call.ec2.describe_instances")

        assert service1 == service2 == "ec2"
        assert operation1 == operation2 == "describe_instances"
        assert len(handler._event_cache) == 1

    def test_thread_safety(self, handler):
        """Test thread safety of request tracking."""
        import threading
        import time

        def add_request():
            request_id = handler._generate_request_id()
            context = RequestContext(
                service="ec2", operation="describe_instances", start_time=time.time()
            )
            with handler._request_lock:
                handler._active_requests[request_id] = context

        # Create multiple threads
        threads = []
        for i in range(10):
            thread = threading.Thread(target=add_request)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify all requests were added safely
        assert len(handler._active_requests) == 10


class TestRequestContext:
    """Test suite for RequestContext dataclass."""

    def test_request_context_creation(self):
        """Test RequestContext creation with default values."""
        context = RequestContext(service="ec2", operation="describe_instances", start_time=123.456)

        assert context.service == "ec2"
        assert context.operation == "describe_instances"
        assert context.start_time == 123.456
        assert context.retry_count == 0
        assert context.region == "unknown"
        assert context.request_size == 0

    def test_request_context_with_all_fields(self):
        """Test RequestContext creation with all fields specified."""
        context = RequestContext(
            service="s3",
            operation="list_buckets",
            start_time=456.789,
            retry_count=2,
            region="eu-west-1",
            request_size=1024,
        )

        assert context.service == "s3"
        assert context.operation == "list_buckets"
        assert context.start_time == 456.789
        assert context.retry_count == 2
        assert context.region == "eu-west-1"
        assert context.request_size == 1024


class TestIntegrationWithMoto:
    """Integration tests with moto library."""

    @pytest.fixture
    def instrumented_session(self):
        """Create instrumented boto3 session."""
        metrics = Mock(spec=MetricsCollector)
        logger = Mock(spec=LoggingPort)
        handler = BotocoreMetricsHandler(metrics, logger)

        session = boto3.Session()
        handler.register_events(session)

        return session, handler, metrics, logger

    @mock_aws
    def test_ec2_operations_with_metrics(self, instrumented_session):
        """Test EC2 operations with metrics collection."""
        session, handler, metrics, logger = instrumented_session

        ec2 = session.client("ec2", region_name="us-east-1")

        # Test various EC2 operations
        ec2.describe_instances()
        ec2.describe_availability_zones()

        # Verify metrics were collected for both operations
        call_args = metrics.increment_counter.call_args_list
        assert any("aws.ec2.describe_instances.calls_total" in str(call) for call in call_args)
        assert any(
            "aws.ec2.describe_availability_zones.calls_total" in str(call) for call in call_args
        )

    @mock_aws
    def test_error_scenarios_with_moto(self, instrumented_session):
        """Test error scenarios using moto."""
        session, handler, metrics, logger = instrumented_session

        ec2 = session.client("ec2", region_name="us-east-1")

        # Test invalid instance ID error
        with pytest.raises(Exception):
            ec2.describe_instances(InstanceIds=["i-invalid"])

        # Verify error metrics were collected
        call_args = metrics.increment_counter.call_args_list
        assert any(call.args[0] == "aws.ec2.describe_instances.errors_total" for call in call_args)

    @mock_aws
    def test_timing_metrics_with_moto(self, instrumented_session):
        """Test timing metrics collection with moto."""
        session, handler, metrics, logger = instrumented_session

        ec2 = session.client("ec2", region_name="us-east-1")

        # Make API call
        ec2.describe_instances()

        # Verify timing metrics were recorded
        metrics.record_time.assert_called()

        # Check that duration was recorded
        call_args = metrics.record_time.call_args_list
        assert any("aws.ec2.describe_instances.duration" in str(call) for call in call_args)


@pytest.mark.integration
class TestAWSMetricsIntegration:
    """Integration tests for AWS metrics with real components."""

    @pytest.fixture
    async def app_with_metrics(self):
        """Create application instance with metrics enabled."""
        # This would be implemented when the full application bootstrap is available
        pass

    def test_metrics_configuration_loading(self):
        """Test loading of AWS metrics configuration."""
        from config.metrics_config import (
            DEFAULT_AWS_METRICS_CONFIG,
            TEST_AWS_METRICS_CONFIG,
            create_aws_metrics_config,
        )

        # Test default configuration
        assert DEFAULT_AWS_METRICS_CONFIG.enabled is True
        assert DEFAULT_AWS_METRICS_CONFIG.sample_rate == 1.0

        # Test test configuration
        assert TEST_AWS_METRICS_CONFIG.environment == "test"
        assert TEST_AWS_METRICS_CONFIG.track_payload_sizes is False

        # Test custom configuration creation
        custom_config = create_aws_metrics_config(
            enabled=True, environment="staging", sample_rate=0.5
        )
        assert custom_config.enabled is True
        assert custom_config.environment == "staging"
        assert custom_config.sample_rate == 0.5

    def test_aws_metrics_definitions(self):
        """Test AWS metrics definitions."""
        from monitoring.aws_metrics import (
            AWS_METRICS,
            get_metric_definition,
            get_service_metrics,
            is_counter_metric,
            is_timer_metric,
        )

        # Test metric definitions exist
        assert "aws_api_calls_total" in AWS_METRICS
        assert "aws_api_duration_ms" in AWS_METRICS

        # Test metric definition retrieval
        calls_metric = get_metric_definition("aws_api_calls_total")
        assert calls_metric is not None
        assert calls_metric.name == "aws_api_calls_total"

        # Test service-specific metrics
        ec2_metrics = get_service_metrics("ec2")
        assert len(ec2_metrics) > 0
        assert any("run_instances" in metric for metric in ec2_metrics)

        # Test metric type checking
        assert is_counter_metric("aws_api_calls_total") is True
        assert is_timer_metric("aws_api_duration_ms") is True
        assert is_counter_metric("aws_api_duration_ms") is False


if __name__ == "__main__":
    pytest.main([__file__])
