"""Tests for metrics tracing configuration exposure."""

import json
from pathlib import Path

from config.manager import ConfigurationManager
from infrastructure.adapters.configuration_adapter import ConfigurationAdapter
from monitoring.metrics import MetricsCollector


def test_default_config_includes_tracing_keys(tmp_path):
    """Verify default_config.json includes tracing configuration keys."""
    config_file = Path(__file__).parent.parent.parent.parent / "config" / "default_config.json"

    with config_file.open() as f:
        config = json.load(f)

    assert "metrics" in config
    metrics_config = config["metrics"]

    # Verify tracing keys are present
    assert "trace_enabled" in metrics_config
    assert "trace_buffer_size" in metrics_config
    assert "trace_file_max_size_mb" in metrics_config

    # Verify default values
    assert metrics_config["trace_enabled"] is False
    assert metrics_config["trace_buffer_size"] == 1000
    assert metrics_config["trace_file_max_size_mb"] == 10


def test_configuration_adapter_includes_tracing_defaults(tmp_path):
    """Verify ConfigurationAdapter.get_metrics_config includes tracing defaults."""
    # Create minimal config
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"metrics": {}}))

    config_manager = ConfigurationManager(config_file=str(config_file))
    adapter = ConfigurationAdapter(config_manager)

    metrics_config = adapter.get_metrics_config()

    # Verify tracing keys are present with defaults
    assert "trace_enabled" in metrics_config
    assert "trace_buffer_size" in metrics_config
    assert "trace_file_max_size_mb" in metrics_config

    assert metrics_config["trace_enabled"] is False
    assert metrics_config["trace_buffer_size"] == 1000
    assert metrics_config["trace_file_max_size_mb"] == 10


def test_configuration_adapter_respects_tracing_overrides(tmp_path):
    """Verify ConfigurationAdapter respects tracing config overrides."""
    # Create config with tracing enabled
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "metrics": {
                    "trace_enabled": True,
                    "trace_buffer_size": 500,
                    "trace_file_max_size_mb": 5,
                }
            }
        )
    )

    config_manager = ConfigurationManager(config_file=str(config_file))
    adapter = ConfigurationAdapter(config_manager)

    metrics_config = adapter.get_metrics_config()

    # Verify overrides are applied
    assert metrics_config["trace_enabled"] is True
    assert metrics_config["trace_buffer_size"] == 500
    assert metrics_config["trace_file_max_size_mb"] == 5


def test_metrics_collector_receives_tracing_config(tmp_path):
    """Verify MetricsCollector receives tracing config from adapter."""
    # Create config with tracing enabled
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "metrics": {
                    "metrics_dir": str(tmp_path / "metrics"),
                    "trace_enabled": True,
                    "trace_buffer_size": 100,
                }
            }
        )
    )

    config_manager = ConfigurationManager(config_file=str(config_file))
    adapter = ConfigurationAdapter(config_manager)

    metrics_config = adapter.get_metrics_config()
    collector = MetricsCollector(metrics_config)

    # Verify collector has tracing enabled
    assert collector.trace_enabled is True
    assert collector._trace_buffer is not None
    assert collector._trace_buffer.maxlen == 100


def test_end_to_end_tracing_via_config(tmp_path):
    """End-to-end test: enable tracing via config and verify it works."""
    # Create config with tracing enabled
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "metrics": {
                    "metrics_dir": str(tmp_path / "metrics"),
                    "trace_enabled": True,
                    "trace_buffer_size": 10,
                }
            }
        )
    )

    config_manager = ConfigurationManager(config_file=str(config_file))
    adapter = ConfigurationAdapter(config_manager)

    metrics_config = adapter.get_metrics_config()
    collector = MetricsCollector(metrics_config)

    # Record some timings
    collector.record_time("test_operation", 0.123)
    collector.record_time("another_operation", 0.456)

    # Verify traces were recorded
    traces = collector.get_traces()
    assert len(traces) == 2
    assert traces[0]["name"] == "test_operation"
    assert traces[1]["name"] == "another_operation"
