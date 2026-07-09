"""
AWS API metrics collection using botocore event hooks.

This module provides centralized metrics collection for all AWS API calls
by leveraging boto3's native event system for minimal-overhead instrumentation.

Metrics are recorded via the OpenTelemetry Meter API so that they surface on
the shared ``prometheus_client.REGISTRY`` (when the Prometheus reader is
configured) without requiring the homegrown ``MetricsCollector``.  When the
OTel SDK or API is not installed the handler acquires a **no-op Meter** and
continues to function — boto3 events fire normally, the record calls become
no-ops.

Instrument layout (OTel names → Prometheus names via dot→underscore + unit):
  orb.aws.api.calls           Counter  {service, operation}
  orb.aws.api.errors          Counter  {service, operation, error_code, error_type}
  orb.aws.api.successes       Counter  {service, operation}
  orb.aws.api.retries         Counter  {service, operation}
  orb.aws.api.throttles       Counter  {service, operation, error_code}
  orb.aws.api.duration        Histogram(seconds)  {service, operation, outcome}
  orb.aws.api.response_size   Histogram(bytes)    {service, operation}
  orb.aws.api.request_size    Histogram(bytes)    {service, operation}

Conscious drops vs the old MetricsCollector-based handler:
  - ``record_time`` rolling arithmetic-mean gauge replaced by Histogram;
    backend computes percentiles from histogram buckets.
  - Unbounded ``self.timers[name]`` list (memory leak) is gone.
  - Name-embedded dimensions (``aws.{service}.{operation}.calls_total``)
    replaced by labelled instruments — no key-space explosion.
"""

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from botocore.exceptions import ClientError

from orb.domain.base.ports import LoggingPort

# ---------------------------------------------------------------------------
# OTel API import — guard so AWS provider works without the monitoring extra.
# Matches the silent-skip pattern in core_services.py.
# ---------------------------------------------------------------------------
try:
    from opentelemetry import metrics as _otel_metrics  # type: ignore[import-not-found]

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OTEL_AVAILABLE = False


def _get_meter(name: str):  # type: ignore[return]
    """Return an OTel Meter, or a no-op stub when the API is absent."""
    if _OTEL_AVAILABLE:
        return _otel_metrics.get_meter(name)
    return _NoOpMeter()


class _NoOpCounter:
    """Stub counter used when OTel API is unavailable."""

    def add(self, amount: float, attributes: Optional[dict] = None) -> None:
        pass


class _NoOpHistogram:
    """Stub histogram used when OTel API is unavailable."""

    def record(self, amount: float, attributes: Optional[dict] = None) -> None:
        pass


class _NoOpMeter:
    """Stub meter returned when opentelemetry-api is not installed."""

    def create_counter(self, name: str, **kwargs) -> _NoOpCounter:  # type: ignore[return]
        return _NoOpCounter()

    def create_histogram(self, name: str, **kwargs) -> _NoOpHistogram:  # type: ignore[return]
        return _NoOpHistogram()


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
    """Centralized AWS API metrics collection using botocore events.

    Writes to an OTel Meter (``opentelemetry.metrics.get_meter``) instead of
    the homegrown ``MetricsCollector``.  Service, operation, outcome, and
    error code are OTel *attributes* (Prometheus labels) rather than being
    embedded in metric names.
    """

    def __init__(
        self,
        logger: LoggingPort,
        aws_metrics_config: Optional[dict[str, Any]] = None,
    ):
        self.logger = logger

        cfg = aws_metrics_config or {}
        self.enabled = bool(cfg.get("provider_metrics_enabled", False))
        _raw_rate = cfg.get("sample_rate", 1.0)
        self.sample_rate = float(_raw_rate) if _raw_rate is not None else 1.0
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

        # Error classification — exactly 5 throttle codes (must be preserved)
        self._throttling_errors = {
            "Throttling",
            "ThrottlingException",
            "RequestLimitExceeded",
            "TooManyRequestsException",
            "ProvisionedThroughputExceededException",
        }

        # ------------------------------------------------------------------ #
        # OTel instruments                                                     #
        # Acquired once at construction; get_meter() returns a no-op when     #
        # the SDK is not configured — instruments are then no-ops too.         #
        # ------------------------------------------------------------------ #
        meter = _get_meter(__name__)

        # Call counters
        self._calls_counter = meter.create_counter(
            "orb.aws.api.calls",
            unit="1",
            description="Total AWS API calls, labelled by service and operation.",
        )
        self._errors_counter = meter.create_counter(
            "orb.aws.api.errors",
            unit="1",
            description="Total AWS API errors, labelled by service, operation, error_code, and error_type.",
        )
        self._successes_counter = meter.create_counter(
            "orb.aws.api.successes",
            unit="1",
            description="Total successful AWS API calls, labelled by service and operation.",
        )
        self._retries_counter = meter.create_counter(
            "orb.aws.api.retries",
            unit="1",
            description="Total AWS API retries, labelled by service and operation.",
        )
        self._throttles_counter = meter.create_counter(
            "orb.aws.api.throttles",
            unit="1",
            description="Total throttled AWS API calls, labelled by service, operation, and error_code.",
        )

        # Duration histogram — replaces the old rolling-average gauge
        self._duration_histogram = meter.create_histogram(
            "orb.aws.api.duration",
            unit="s",
            description="AWS API call duration in seconds, labelled by service, operation, and outcome.",
        )

        # Payload size histograms (only populated when track_payload_sizes=True)
        self._response_size_histogram = meter.create_histogram(
            "orb.aws.api.response_size",
            unit="By",
            description="Estimated AWS API response payload size in bytes.",
        )
        self._request_size_histogram = meter.create_histogram(
            "orb.aws.api.request_size",
            unit="By",
            description="Estimated AWS API request payload size in bytes.",
        )

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

            # Apply deterministic every-Nth-call sampling (modulo _sample_counter, NOT random)
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

            # Record call counter with OTel labels
            attrs = {"service": service, "operation": operation}
            self._calls_counter.add(1, attrs)

            # Record request size if tracking enabled
            if self.track_payload_sizes and request_size > 0:
                self._request_size_histogram.record(
                    float(request_size), {"service": service, "operation": operation}
                )

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

            # Calculate duration
            duration_s = time.perf_counter() - context.start_time
            response_size = self._estimate_response_size(kwargs.get("parsed", {}))

            # Determine status code if available
            http_response = kwargs.get("http_response")
            status_code = getattr(http_response, "status_code", 200)

            attrs_base = {"service": context.service, "operation": context.operation}

            # Record duration histogram
            if status_code and status_code >= 400:
                outcome = "error"
            else:
                outcome = "success"

            self._duration_histogram.record(
                duration_s,
                {**attrs_base, "outcome": outcome},
            )

            # Record response size
            if self.track_payload_sizes and response_size > 0:
                self._response_size_histogram.record(float(response_size), attrs_base)

            if status_code and status_code >= 400:
                self._errors_counter.add(
                    1,
                    {**attrs_base, "error_code": "HTTPError", "error_type": "HTTPError"},
                )
            else:
                self._successes_counter.add(1, attrs_base)

            # Record retry metrics if retries occurred
            if context.retry_count > 0:
                self._retries_counter.add(context.retry_count, attrs_base)

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

            # Calculate duration
            duration_s = time.perf_counter() - context.start_time

            # Extract error information
            exception = kwargs.get("exception")
            if exception:
                error_code, error_type = self._parse_error(exception)
            else:
                error_code, error_type = "Unknown", "Unknown"

            attrs_base = {"service": context.service, "operation": context.operation}

            # Record duration histogram with error outcome
            self._duration_histogram.record(
                duration_s,
                {**attrs_base, "outcome": "error"},
            )

            # Record error counter with classification labels
            self._errors_counter.add(
                1,
                {**attrs_base, "error_code": error_code, "error_type": error_type},
            )

            # Special handling for throttling — exactly the 5 known codes
            if self._is_throttling_error(error_code):
                self._throttles_counter.add(
                    1,
                    {**attrs_base, "error_code": error_code},
                )

        except Exception as e:
            self.logger.warning(f"Error in after_call_error handler: {e}")

    def _on_retry_needed(self, event_name: str, **kwargs) -> None:
        """Handle retry decision events — increments retry_count on the context."""
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
        """Handle before-retry events — emits a retry counter tick."""
        try:
            service, operation = self._parse_event_name(event_name)
            self._retries_counter.add(1, {"service": service, "operation": operation})
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
        """Check if error code indicates throttling (exactly 5 recognised codes)."""
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
        """Determine if this request should be sampled.

        Uses deterministic every-Nth-call modulo logic (NOT random) so
        sampling is reproducible — critical for compliance use cases.
        """
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
