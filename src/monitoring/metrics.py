"""Application metrics collection and monitoring."""

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

# Module-level constant to avoid B008 warning
DEFAULT_MAX_AGE = timedelta(days=7)


@dataclass
class Metric:
    """Base class for metrics."""

    name: str
    value: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    labels: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert metric to dictionary."""
        return {
            "name": self.name,
            "value": self.value,
            "timestamp": self.timestamp.isoformat(),
            "labels": self.labels,
        }


@dataclass
class Counter(Metric):
    """Counter metric that only increases."""

    def increment(self, value: float = 1.0) -> None:
        """Increment counter value."""
        self.value += value
        self.timestamp = datetime.utcnow()


@dataclass
class Gauge(Metric):
    """Gauge metric that can go up and down."""

    def set(self, value: float) -> None:
        """Set gauge value."""
        self.value = value
        self.timestamp = datetime.utcnow()


@dataclass
class Timer:
    """Timer for measuring durations."""

    name: str
    labels: Dict[str, str]
    start_time: float = field(default_factory=time.time)

    def stop(self) -> float:
        """Stop timer and return duration."""
        duration = time.time() - self.start_time
        return duration


class MetricsCollector:
    """Collects and manages application metrics."""

    def __init__(self, config: Dict[str, Any]) -> None:
        """Initialize metrics collector."""
        self.config = config
        self.metrics: Dict[str, Metric] = {}
        self.timers: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

        # Create metrics directory
        self.metrics_dir = Path(config.get("METRICS_DIR", "./metrics"))
        self.metrics_dir.mkdir(parents=True, exist_ok=True)

        # Initialize default metrics
        self._initialize_metrics()

        # Start background metrics writer if enabled
        if config.get("METRICS_ENABLED", True):
            self._start_metrics_writer()

    def _initialize_metrics(self) -> None:
        """Initialize default metrics."""
        # Request metrics
        self.register_counter("requests_total", labels={"type": "all"})
        self.register_counter("requests_failed_total", labels={"type": "all"})

        # AWS metrics
        self.register_counter("aws_api_calls_total", labels={"service": "all"})
        self.register_counter("aws_api_errors_total", labels={"service": "all"})

        # Resource metrics
        self.register_gauge("active_instances", labels={"type": "all"})
        self.register_gauge("pending_requests", labels={"type": "all"})

        # Performance metrics
        self.register_gauge("response_time_seconds", labels={"endpoint": "all"})
        self.register_gauge("memory_usage_bytes")
        self.register_gauge("cpu_usage_percent")

    def register_counter(self, name: str, labels: Optional[Dict[str, str]] = None) -> Counter:
        """Register a new counter metric."""
        with self._lock:
            counter = Counter(name, 0.0, labels=labels or {})
            self.metrics[name] = counter
            return counter

    def register_gauge(self, name: str, labels: Optional[Dict[str, str]] = None) -> Gauge:
        """Register a new gauge metric."""
        with self._lock:
            gauge = Gauge(name, 0.0, labels=labels or {})
            self.metrics[name] = gauge
            return gauge

    def increment_counter(self, name: str, value: float = 1.0) -> None:
        """Increment a counter metric."""
        with self._lock:
            if name not in self.metrics:
                self.register_counter(name)
            if isinstance(self.metrics[name], Counter):
                self.metrics[name].increment(value)

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge metric value."""
        with self._lock:
            if name not in self.metrics:
                self.register_gauge(name)
            if isinstance(self.metrics[name], Gauge):
                self.metrics[name].set(value)

    def start_timer(self, name: str = "", labels: Optional[Dict[str, str]] = None) -> Timer:
        """Start a new timer."""
        if labels is None:
            labels = {}
        return Timer(name, labels)

    def record_time(self, name: str, duration: float) -> None:
        """Record a timing duration."""
        with self._lock:
            if name not in self.timers:
                self.timers[name] = []
            self.timers[name].append(duration)

            # Calculate and update average response time
            avg_time = sum(self.timers[name]) / len(self.timers[name])
            self.set_gauge(f"{name}_seconds", avg_time)

    def record_success(
        self,
        operation: str,
        start_time: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a successful operation."""
        duration = time.time() - start_time
        self.increment_counter(f"{operation}_success_total")
        self.record_time(f"{operation}_duration", duration)

        if metadata:
            logger.info(
                "%s completed successfully",
                operation,
                extra={"duration": duration, "metadata": metadata},
            )

    def record_error(
        self,
        operation: str,
        start_time: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a failed operation."""
        duration = time.time() - start_time
        self.increment_counter(f"{operation}_error_total")
        self.record_time(f"{operation}_error_duration", duration)

        if metadata:
            logger.error(
                "%s failed",
                operation,
                extra={"duration": duration, "metadata": metadata},
            )

    def get_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Get all current metrics."""
        with self._lock:
            return {name: metric.to_dict() for name, metric in self.metrics.items()}

    def _start_metrics_writer(self) -> None:
        """Start background metrics writer thread."""

        def write_metrics() -> None:
            """Write metrics to file periodically in background thread."""
            while True:
                try:
                    metrics = self.get_metrics()

                    # Write to JSON file
                    metrics_file = self.metrics_dir / "metrics.json"
                    with metrics_file.open("w") as f:
                        json.dump(metrics, f, indent=2)

                    # Write to Prometheus format
                    prom_file = self.metrics_dir / "metrics.prom"
                    with prom_file.open("w") as f:
                        for name, metric in metrics.items():
                            labels = ",".join(f'{k}="{v}"' for k, v in metric["labels"].items())
                            f.write(f"{name}{{{labels}}} {metric['value']}\n")

                    time.sleep(self.config.get("METRICS_INTERVAL", 60))

                except Exception as e:
                    logger.error("Error writing metrics: %s", e)
                    time.sleep(5)  # Shorter sleep on error

        thread = threading.Thread(target=write_metrics, daemon=True)
        thread.start()

    def check_thresholds(self) -> List[Dict[str, Any]]:
        """Check metrics against configured thresholds."""
        alerts = []
        thresholds = self.config.get("ALERT_THRESHOLDS", {})

        with self._lock:
            for name, threshold in thresholds.items():
                if name in self.metrics:
                    metric = self.metrics[name]
                    if metric.value > threshold:
                        alerts.append(
                            {
                                "metric": name,
                                "value": metric.value,
                                "threshold": threshold,
                                "timestamp": datetime.utcnow().isoformat(),
                            }
                        )

        return alerts

    def reset_metrics(self) -> None:
        """Reset all metrics to their initial values."""
        with self._lock:
            for metric in self.metrics.values():
                if isinstance(metric, Counter):
                    metric.value = 0.0
                elif isinstance(metric, Gauge):
                    metric.value = 0.0
            self.timers.clear()

    def cleanup_old_metrics(self, max_age: timedelta = DEFAULT_MAX_AGE) -> None:
        """Clean up old metrics data."""
        cutoff = datetime.utcnow() - max_age

        with self._lock:
            # Clean up timers
            for name in list(self.timers.keys()):
                if not self.timers[name]:
                    del self.timers[name]

            # Clean up old metric files
            for file in self.metrics_dir.glob("*.json"):
                if file.stat().st_mtime < cutoff.timestamp():
                    try:
                        file.unlink()
                    except Exception as e:
                        logger.warning("Failed to delete old metrics file %s: %s", file, e)
