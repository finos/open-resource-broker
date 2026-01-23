"""
AWS API metrics collection using botocore event hooks.

This module provides centralized metrics collection for all AWS API calls
by leveraging boto3's native event system for minimal-overhead instrumentation.
"""

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from botocore.exceptions import ClientError

from domain.base.ports import LoggingPort
from monitoring.metrics import MetricsCollector


@dataclass
class RequestContext:
    """Context information for tracking AWS API requests."""

    service: str
    operation: str
    start_time: float
    retry_count: int = 0
    region: str = "unknown"
    request_size: int = 0


class BotocoreMetricsHandler:
    """Centralized AWS API metrics collection using botocore events."""

    def __init__(
        self,
        metrics_collector: MetricsCollector,
        logger: LoggingPort,
        aws_metrics_config: Optional[dict[str, Any]] = None,
    ):
        self.metrics = metrics_collector
        self.logger = logger

        cfg = aws_metrics_config or {}
        self.enabled = bool(cfg.get("aws_metrics_enabled", False))
        self.sample_rate = float(cfg.get("sample_rate", 1.0) or 1.0)
        self.monitored_services = set(cfg.get("monitored_services", []) or [])
        self.monitored_operations = set(cfg.get("monitored_operations", []) or [])
        self.track_payload_sizes = bool(cfg.get("track_payload_sizes", False))

        # Check if AWS metrics are enabled
        if not self.enabled:
            self.logger.debug("AWS metrics collection is disabled via configuration")
            return

        # Thread-safe request tracking
        self._active_requests: Dict[str, RequestContext] = {}
        self._request_lock = threading.RLock()
        self._request_counter = 0
        self._sample_counter = 0  # Dedicated counter for sampling decisions

        # Performance optimizations
        self._event_pattern = re.compile(r"(before|after)-call\.([^.]+)\.([^.]+)")
        self._event_cache: Dict[str, tuple] = {}

        # Error classification
        self._throttling_errors = {
            "Throttling",
            "ThrottlingException",
            "RequestLimitExceeded",
            "TooManyRequestsException",
            "ProvisionedThroughputExceededException",
        }

    def register_events(self, session) -> None:
        """Register event handlers with boto3 session only if metrics are enabled."""
        # Guard: Only register events if AWS metrics are enabled
        if not self.enabled:
            self.logger.debug("AWS metrics are disabled - skipping event registration")
            return

        # Ensure client-level emitters get the handlers by wrapping client()
        original_client = session.client

        def instrumented_client(*args, **kwargs):
            client = original_client(*args, **kwargs)
            self._register_client_events(client)
            return client

        session.client = instrumented_client
        self.logger.info("AWS API metrics collection enabled via botocore events")

    def _before_call(self, event_name: str, **kwargs) -> None:
        """Handle before-call event: start timing and count requests."""
        try:
            service, operation = self._parse_event_name(event_name)

            if service == "unknown" or operation == "unknown":
                self.logger.warning(f"Unrecognized event name for metrics: {event_name}")
                return

            # Check if metrics are disabled
            if not self.enabled:
                return

            # Check service filtering
            if self.monitored_services and service not in self.monitored_services:
                return

            # Check operation inclusion filter if provided
            if self.monitored_operations and operation not in self.monitored_operations:
                return

            # Apply sampling
            if not self._should_sample():
                return

            request_id = self._generate_request_id()

            # Propagate request ID and context via request_context if available
            request_context = kwargs.get("context")
            if isinstance(request_context, dict):
                request_context["metrics_request_id"] = request_id
            else:
                request_context = {}
                kwargs["context"] = request_context
            request_dict = kwargs.get("request_dict")
            if isinstance(request_dict, dict):
                request_dict["metrics_request_id"] = request_id

            # Extract request metadata
            endpoint = kwargs.get("endpoint", {})
            region = getattr(endpoint, "region_name", "unknown")
            request_size = (
                self._estimate_request_size(kwargs.get("params", {}))
                if self.track_payload_sizes
                else 0
            )

            # Create request context
            context = RequestContext(
                service=service,
                operation=operation,
                start_time=time.perf_counter(),
                region=region,
                request_size=request_size,
            )

            # Store context thread-safely and attach to request_context for after-call handlers
            with self._request_lock:
                self._active_requests[request_id] = context
            request_context["metrics_context"] = context

            # Record metrics (align with current MetricsCollector API: no labels/histograms)
            self.metrics.increment_counter(f"aws.{service}.{operation}.calls_total")
            self.metrics.increment_counter("aws_api_calls_total")

            # Store request ID for correlation
            if "request_dict" in kwargs:
                kwargs["request_dict"]["metrics_request_id"] = request_id

        except Exception as e:
            self.logger.warning(f"Error in before_call handler: {e}")

    def _after_call_success(self, event_name: str, **kwargs) -> None:
        """Handle successful API call completion."""
        try:
            context = None
            request_context = kwargs.get("context")
            if isinstance(request_context, dict):
                context = request_context.get("metrics_context")

            if not context:
                request_id = self._extract_request_id(kwargs)
                if request_id:
                    context = self._pop_request_context(request_id)
            if not context:
                with self._request_lock:
                    if len(self._active_requests) == 1:
                        _, context = self._active_requests.popitem()
            if not context:
                return

            # Calculate metrics
            duration_ms = (time.perf_counter() - context.start_time) * 1000
            response_size = self._estimate_response_size(kwargs.get("parsed", {}))

            # Determine status code if available
            http_response = kwargs.get("http_response")
            status_code = getattr(http_response, "status_code", 200)

            # Record metrics (using current collector)
            self.metrics.record_time(
                f"aws.{context.service}.{context.operation}.duration",
                duration_ms / 1000.0,
            )

            # Record response size metrics
            self.metrics.set_gauge(
                f"aws.{context.service}.{context.operation}.response_size",
                response_size,
            )
            self.metrics.set_gauge("aws_api_response_size", response_size)

            if status_code and status_code >= 400:
                self.metrics.increment_counter(
                    f"aws.{context.service}.{context.operation}.errors_total"
                )
                self.metrics.increment_counter("aws_api_errors_total")
            else:
                self.metrics.increment_counter(
                    f"aws.{context.service}.{context.operation}.success_total"
                )
                self.metrics.increment_counter("aws_api_success_total")

            # Record retry metrics if retries occurred
            if context.retry_count > 0:
                self.metrics.increment_counter(
                    f"aws.{context.service}.{context.operation}.retries_total"
                )

        except Exception as e:
            self.logger.warning(f"Error in after_call_success handler: {e}")

    def _after_call_error(self, event_name: str, **kwargs) -> None:
        """Handle failed API call completion."""
        try:
            context = None
            request_context = kwargs.get("context")
            if isinstance(request_context, dict):
                context = request_context.get("metrics_context")

            if not context:
                request_id = self._extract_request_id(kwargs)
                if request_id:
                    context = self._pop_request_context(request_id)
            if not context:
                with self._request_lock:
                    if len(self._active_requests) == 1:
                        _, context = self._active_requests.popitem()
            if not context:
                return

            # Calculate metrics
            duration_ms = (time.perf_counter() - context.start_time) * 1000

            # Extract error information
            exception = kwargs.get("exception")
            if exception:
                error_code, error_type = self._parse_error(exception)
            else:
                error_code, error_type = "Unknown", "Unknown"

            # Record error metrics (using current collector)
            self.metrics.record_time(
                f"aws.{context.service}.{context.operation}.duration",
                duration_ms / 1000.0,
            )
            self.metrics.increment_counter(
                f"aws.{context.service}.{context.operation}.errors_total"
            )
            self.metrics.increment_counter("aws_api_errors_total")

            # Record error type classification
            self.metrics.increment_counter(
                f"aws.{context.service}.{context.operation}.errors.{error_type.lower()}"
            )
            self.metrics.increment_counter(f"aws_api_errors_{error_type.lower()}_total")

            # Special handling for throttling
            if self._is_throttling_error(error_code):
                self.metrics.increment_counter(
                    f"aws.{context.service}.{context.operation}.throttling_total"
                )
                self.metrics.increment_counter("aws_api_throttling_total")

        except Exception as e:
            self.logger.warning(f"Error in after_call_error handler: {e}")

    def _on_retry_needed(self, event_name: str, **kwargs) -> None:
        """Handle retry decision events."""
        try:
            context = None
            request_context = kwargs.get("request_context")
            if isinstance(request_context, dict):
                context = request_context.get("metrics_context")
            if not context:
                request_id = self._extract_request_id(kwargs)
                if request_id:
                    with self._request_lock:
                        context = self._active_requests.get(request_id)
            if context:
                context.retry_count += 1
        except Exception as e:
            self.logger.warning(f"Error in retry_needed handler: {e}")

    def _before_retry(self, event_name: str, **kwargs) -> None:
        """Handle before retry events."""
        try:
            service, operation = self._parse_event_name(event_name)

            self.metrics.increment_counter(f"aws.{service}.{operation}.retries_total")
            self.metrics.increment_counter("aws_api_retries_total")

        except Exception as e:
            self.logger.warning(f"Error in before_retry handler: {e}")

    def _register_client_events(self, client) -> None:
        """Register handlers on a specific boto3 client emitter."""
        events = client.meta.events
        events.register("before-call", self._before_call)
        events.register("after-call", self._after_call_success)
        events.register("after-call-error", self._after_call_error)
        events.register("needs-retry", self._on_retry_needed)
        events.register("before-retry", self._before_retry)

    # Helper methods

    def _parse_event_name(self, event_name: str) -> tuple[str, str]:
        """Parse botocore event name to extract service and operation."""
        if event_name in self._event_cache:
            return self._event_cache[event_name]

        match = self._event_pattern.match(event_name)
        if match:
            service, operation = match.groups()[1:3]
            operation = self._normalize_operation_name(operation)
            self._event_cache[event_name] = (service, operation)
            return service, operation

        # Fallback parsing
        parts = event_name.split(".")
        if len(parts) >= 3:
            service, operation = parts[1], parts[2]
            operation = self._normalize_operation_name(operation)
            self._event_cache[event_name] = (service, operation)
            return service, operation

        return "unknown", "unknown"

    def _normalize_operation_name(self, operation: str) -> str:
        """Normalize operation name to snake_case for metric consistency."""
        import re as _re

        snake = _re.sub(r"(?<!^)(?=[A-Z])", "_", operation).lower()
        return snake

    def _generate_request_id(self) -> str:
        """Generate unique request ID for correlation."""
        with self._request_lock:
            self._request_counter += 1
            return f"req_{threading.get_ident()}_{self._request_counter}"

    def _extract_request_id(self, kwargs: dict) -> Optional[str]:
        """Extract request ID from event kwargs."""
        request_dict = kwargs.get("request_dict", {}) or {}
        if isinstance(request_dict, dict):
            rid = request_dict.get("metrics_request_id")
            if rid:
                return rid

        request_context = kwargs.get("context", {}) or {}
        if isinstance(request_context, dict):
            rid = request_context.get("metrics_request_id")
            if rid:
                return rid

        return None

    def _pop_request_context(self, request_id: str) -> Optional[RequestContext]:
        """Remove and return request context."""
        with self._request_lock:
            return self._active_requests.pop(request_id, None)

    def _parse_error(self, exception: Exception) -> tuple[str, str]:
        """Parse exception to extract error code and type."""
        if isinstance(exception, ClientError):
            error_code = exception.response.get("Error", {}).get("Code", "Unknown")
            error_type = "ClientError"
        else:
            error_code = "Unknown"
            error_type = type(exception).__name__ if exception else "Unknown"

        return error_code, error_type

    def _is_throttling_error(self, error_code: str) -> bool:
        """Check if error code indicates throttling."""
        return error_code in self._throttling_errors

    def _estimate_request_size(self, params: dict) -> int:
        """Estimate request payload size."""
        if not params:
            return 0
        try:
            import json

            return len(json.dumps(params, default=str))
        except Exception:
            return 0

    def _estimate_response_size(self, response: dict) -> int:
        """Estimate response payload size."""
        if not response:
            return 0
        try:
            import json

            return len(json.dumps(response, default=str))
        except Exception:
            return 0

    def _should_sample(self) -> bool:
        """Determine if this request should be sampled based on configuration."""
        if self.sample_rate >= 1.0:
            return True
        if self.sample_rate <= 0:
            return False

        # Use dedicated sampling counter to ensure proper sampling logic
        with self._request_lock:
            self._sample_counter += 1
            return (self._sample_counter % int(1.0 / self.sample_rate)) == 0

    def get_stats(self) -> dict:
        """Get handler statistics for monitoring."""
        with self._request_lock:
            return {
                "active_requests": len(self._active_requests),
                "event_cache_size": len(self._event_cache),
                "total_requests_processed": self._request_counter,
            }
