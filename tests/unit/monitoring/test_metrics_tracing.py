"""Tests for MetricsCollector tracing buffer functionality."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from monitoring.metrics import MetricsCollector


def test_tracing_disabled_by_default(tmp_path):
    """Verify tracing is disabled by default."""
    config = {"metrics_dir": str(tmp_path)}
    collector = MetricsCollector(config)

    assert collector.trace_enabled is False
    assert collector._trace_buffer is None
    assert collector.get_traces() == []


def test_tracing_enabled_creates_buffer(tmp_path):
    """Verify tracing enabled creates buffer."""
    config = {
        "metrics_dir": str(tmp_path),
        "trace_enabled": True,
        "trace_buffer_size": 100,
    }
    collector = MetricsCollector(config)

    assert collector.trace_enabled is True
    assert collector._trace_buffer is not None
    assert collector._trace_buffer.maxlen == 100


def test_record_time_adds_trace_when_enabled(tmp_path):
    """Verify record_time adds trace entry when tracing is enabled."""
    config = {
        "metrics_dir": str(tmp_path),
        "trace_enabled": True,
        "trace_buffer_size": 10,
    }
    collector = MetricsCollector(config)

    collector.record_time("test_operation", 0.123)

    traces = collector.get_traces()
    assert len(traces) == 1
    assert traces[0]["name"] == "test_operation"
    assert traces[0]["duration"] == 0.123
    assert "timestamp" in traces[0]


def test_record_time_no_trace_when_disabled(tmp_path):
    """Verify record_time doesn't add trace when tracing is disabled."""
    config = {"metrics_dir": str(tmp_path), "trace_enabled": False}
    collector = MetricsCollector(config)

    collector.record_time("test_operation", 0.123)

    traces = collector.get_traces()
    assert len(traces) == 0


def test_trace_buffer_caps_at_maxlen(tmp_path):
    """Verify trace buffer caps at maxlen (ring buffer behavior)."""
    config = {
        "metrics_dir": str(tmp_path),
        "trace_enabled": True,
        "trace_buffer_size": 2,
    }
    collector = MetricsCollector(config)

    collector.record_time("op1", 0.1)
    collector.record_time("op2", 0.2)
    collector.record_time("op3", 0.3)

    traces = collector.get_traces()
    assert len(traces) == 2
    # Should contain last two entries
    assert traces[0]["name"] == "op2"
    assert traces[1]["name"] == "op3"


def test_get_traces_returns_snapshot(tmp_path):
    """Verify get_traces returns a copy, not the buffer itself."""
    config = {
        "metrics_dir": str(tmp_path),
        "trace_enabled": True,
    }
    collector = MetricsCollector(config)

    collector.record_time("test", 0.1)
    traces1 = collector.get_traces()
    traces2 = collector.get_traces()

    # Should be different list objects
    assert traces1 is not traces2
    # But same content
    assert traces1 == traces2


def test_flush_traces_writes_to_file(tmp_path):
    """Verify flush_traces writes traces to file and clears buffer."""
    config = {
        "metrics_dir": str(tmp_path),
        "trace_enabled": True,
    }
    collector = MetricsCollector(config)

    collector.record_time("op1", 0.1)
    collector.record_time("op2", 0.2)

    collector.flush_traces()

    # Buffer should be empty after flush
    assert len(collector.get_traces()) == 0

    # Check file was created
    trace_files = list(tmp_path.glob("traces_*.jsonl"))
    assert len(trace_files) == 1

    # Verify content
    with trace_files[0].open() as f:
        lines = f.readlines()
    assert len(lines) == 2

    trace1 = json.loads(lines[0])
    assert trace1["name"] == "op1"
    assert trace1["duration"] == 0.1


def test_flush_traces_handles_errors(tmp_path):
    """Verify flush_traces handles write errors gracefully."""
    config = {
        "metrics_dir": str(tmp_path),
        "trace_enabled": True,
    }
    collector = MetricsCollector(config)

    collector.record_time("test", 0.1)

    # Mock file open to raise exception
    with patch("pathlib.Path.open", side_effect=OSError("Disk full")):
        # Should not raise
        collector.flush_traces()

    # Buffer should still be cleared (best effort)
    assert len(collector.get_traces()) == 0


def test_flush_traces_with_empty_buffer(tmp_path):
    """Verify flush_traces handles empty buffer."""
    config = {
        "metrics_dir": str(tmp_path),
        "trace_enabled": True,
    }
    collector = MetricsCollector(config)

    # Should not raise
    collector.flush_traces()

    # No files should be created
    trace_files = list(tmp_path.glob("traces_*.jsonl"))
    assert len(trace_files) == 0


def test_timestamp_format_is_iso8601(tmp_path):
    """Verify traces use ISO8601 timestamp format with timezone."""
    config = {
        "metrics_dir": str(tmp_path),
        "trace_enabled": True,
    }
    collector = MetricsCollector(config)

    collector.record_time("test", 0.1)

    traces = collector.get_traces()
    timestamp_str = traces[0]["timestamp"]

    # Should be parseable as ISO8601
    timestamp = datetime.fromisoformat(timestamp_str)
    assert timestamp.tzinfo is not None

    # Should contain timezone info (ends with +00:00 or Z)
    assert "+" in timestamp_str or timestamp_str.endswith("Z")


def test_flush_traces_creates_timestamped_files(tmp_path):
    """Verify flush_traces creates files with timestamps."""
    config = {
        "metrics_dir": str(tmp_path),
        "trace_enabled": True,
    }
    collector = MetricsCollector(config)

    collector.record_time("test", 0.1)
    collector.flush_traces()

    trace_files = list(tmp_path.glob("traces_*.jsonl"))
    assert len(trace_files) == 1

    # Filename should match pattern traces_YYYYMMDD_HHMMSS.jsonl
    filename = trace_files[0].name
    assert filename.startswith("traces_")
    assert filename.endswith(".jsonl")
    assert len(filename) == len("traces_20251203_102833.jsonl")


def test_concurrent_record_time_thread_safe(tmp_path):
    """Verify concurrent record_time calls are thread-safe."""
    import threading

    config = {
        "metrics_dir": str(tmp_path),
        "trace_enabled": True,
        "trace_buffer_size": 100,
    }
    collector = MetricsCollector(config)

    def record_traces():
        for i in range(10):
            collector.record_time(f"op_{i}", 0.1)

    threads = [threading.Thread(target=record_traces) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    traces = collector.get_traces()
    # Should have recorded some traces (up to buffer size)
    assert len(traces) <= 100
    assert len(traces) > 0


def test_flush_traces_does_not_hold_lock_during_io(tmp_path):
    """Verify flush_traces releases lock before writing to disk."""
    config = {
        "metrics_dir": str(tmp_path),
        "trace_enabled": True,
    }
    collector = MetricsCollector(config)

    collector.record_time("test", 0.1)

    # Track if lock is held during file write
    lock_held_during_write = False
    original_open = Path.open

    def patched_open(self, *args, **kwargs):
        nonlocal lock_held_during_write
        # Try to acquire lock - if we can, it wasn't held
        if collector._lock.acquire(blocking=False):
            collector._lock.release()
        else:
            lock_held_during_write = True
        return original_open(self, *args, **kwargs)

    with patch.object(Path, "open", patched_open):
        collector.flush_traces()

    # Lock should not be held during file write
    assert not lock_held_during_write
