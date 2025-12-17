from infrastructure.adapters.configuration_adapter import ConfigurationAdapter


class FakeConfigManager:
    def __init__(self, raw: dict):
        self._raw = raw

    def _ensure_raw_config(self):
        return self._raw


def test_metrics_config_defaults_when_missing_section():
    adapter = ConfigurationAdapter(FakeConfigManager(raw={}))

    cfg = adapter.get_metrics_config()

    assert cfg["metrics_enabled"] is False
    assert cfg["metrics_dir"] == "./metrics"
    assert cfg["metrics_interval"] == 60
    assert cfg["aws_metrics"]["aws_metrics_enabled"] is False
    assert cfg["aws_metrics"]["sample_rate"] == 1.0
    assert cfg["aws_metrics"]["monitored_services"] == []
    assert cfg["aws_metrics"]["monitored_operations"] == []
    assert cfg["aws_metrics"]["track_payload_sizes"] is False


def test_metrics_config_overrides_top_level_and_nested():
    raw = {
        "metrics": {
            "metrics_enabled": False,
            "metrics_dir": "/tmp/metrics",
            "metrics_interval": 15,
            "aws_metrics": {
                "aws_metrics_enabled": False,
                "sample_rate": 0.5,
                "monitored_services": ["ec2"],
                "monitored_operations": ["DescribeInstances"],
                "track_payload_sizes": False,
            },
        }
    }
    adapter = ConfigurationAdapter(FakeConfigManager(raw=raw))

    cfg = adapter.get_metrics_config()

    assert cfg["metrics_enabled"] is False
    assert cfg["metrics_dir"] == "/tmp/metrics"
    assert cfg["metrics_interval"] == 15

    aws_cfg = cfg["aws_metrics"]
    assert aws_cfg["aws_metrics_enabled"] is False
    assert aws_cfg["sample_rate"] == 0.5
    assert aws_cfg["monitored_services"] == ["ec2"]
    assert aws_cfg["monitored_operations"] == ["DescribeInstances"]
    assert aws_cfg["track_payload_sizes"] is False


def test_metrics_config_partial_overrides_preserve_defaults():
    raw = {
        "metrics": {
            "metrics_dir": "/var/metrics",
            "aws_metrics": {
                "sample_rate": 0.25,
            },
        }
    }
    adapter = ConfigurationAdapter(FakeConfigManager(raw=raw))

    cfg = adapter.get_metrics_config()

    assert cfg["metrics_dir"] == "/var/metrics"
    assert cfg["metrics_enabled"] is False
    assert cfg["metrics_interval"] == 60

    aws_cfg = cfg["aws_metrics"]
    assert aws_cfg["sample_rate"] == 0.25
    assert aws_cfg["aws_metrics_enabled"] is False
    assert aws_cfg["monitored_services"] == []
    assert aws_cfg["monitored_operations"] == []
    assert aws_cfg["track_payload_sizes"] is False
