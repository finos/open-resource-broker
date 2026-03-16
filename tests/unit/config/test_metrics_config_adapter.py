from orb.config.schemas.metrics_schema import MetricsConfig
from orb.infrastructure.adapters.configuration_adapter import ConfigurationAdapter
from orb.infrastructure.adapters.logging_adapter import LoggingAdapter

_logger = LoggingAdapter(__name__)


class FakeConfigManager:
    def __init__(self, raw: dict):
        self._raw = raw

    def _ensure_raw_config(self):
        return self._raw


def test_metrics_config_defaults_when_missing_section():
    adapter = ConfigurationAdapter(FakeConfigManager(raw={}), _logger)

    cfg = adapter.get_metrics_config()

    assert cfg["metrics_enabled"] is False
    assert cfg["metrics_dir"] == "./metrics"
    assert cfg["metrics_interval"] == 60
    assert cfg["provider_metrics"]["provider_metrics_enabled"] is False
    assert cfg["provider_metrics"]["sample_rate"] == 1.0
    assert cfg["provider_metrics"]["monitored_services"] == []
    assert cfg["provider_metrics"]["monitored_operations"] == []
    assert cfg["provider_metrics"]["track_payload_sizes"] is False


def test_metrics_config_overrides_top_level_and_nested():
    raw = {
        "metrics": {
            "metrics_enabled": False,
            "metrics_dir": "/tmp/metrics",
            "metrics_interval": 15,
            "provider_metrics": {
                "provider_metrics_enabled": False,
                "sample_rate": 0.5,
                "monitored_services": ["ec2"],
                "monitored_operations": ["DescribeInstances"],
                "track_payload_sizes": False,
            },
        }
    }
    adapter = ConfigurationAdapter(FakeConfigManager(raw=raw), _logger)

    cfg = adapter.get_metrics_config()

    assert cfg["metrics_enabled"] is False
    assert cfg["metrics_dir"] == "/tmp/metrics"
    assert cfg["metrics_interval"] == 15

    aws_cfg = cfg["provider_metrics"]
    assert aws_cfg["provider_metrics_enabled"] is False
    assert aws_cfg["sample_rate"] == 0.5
    assert aws_cfg["monitored_services"] == ["ec2"]
    assert aws_cfg["monitored_operations"] == ["DescribeInstances"]
    assert aws_cfg["track_payload_sizes"] is False


def test_metrics_config_partial_overrides_preserve_defaults():
    raw = {
        "metrics": {
            "metrics_dir": "/var/metrics",
            "provider_metrics": {
                "sample_rate": 0.25,
            },
        }
    }
    adapter = ConfigurationAdapter(FakeConfigManager(raw=raw), _logger)

    cfg = adapter.get_metrics_config()

    assert cfg["metrics_dir"] == "/var/metrics"
    assert cfg["metrics_enabled"] is False
    assert cfg["metrics_interval"] == 60

    aws_cfg = cfg["provider_metrics"]
    assert aws_cfg["sample_rate"] == 0.25
    assert aws_cfg["provider_metrics_enabled"] is False
    assert aws_cfg["monitored_services"] == []
    assert aws_cfg["monitored_operations"] == []
    assert aws_cfg["track_payload_sizes"] is False


# --- task 1718: backward-compatibility aliases on MetricsConfig ---


def test_metrics_config_old_aws_metrics_key_populates_provider_metrics():
    """aws_metrics alias (old name) must populate provider_metrics field."""
    cfg = MetricsConfig.model_validate({"aws_metrics": {"aws_metrics_enabled": True}})
    assert cfg.provider_metrics.provider_metrics_enabled is True


def test_metrics_config_new_provider_metrics_key_works():
    """provider_metrics (new name) must also be accepted directly."""
    cfg = MetricsConfig.model_validate({"provider_metrics": {"provider_metrics_enabled": True}})
    assert cfg.provider_metrics.provider_metrics_enabled is True
