"""Tests for circuit breaker config wiring in AWSHandler._get_circuit_breaker_config."""

from unittest.mock import MagicMock

from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler

# ---------------------------------------------------------------------------
# Minimal concrete subclass — only implements the abstract methods
# ---------------------------------------------------------------------------


class _ConcreteHandler(AWSHandler):
    def _acquire_hosts_internal(self, request, aws_template):  # type: ignore[override]
        pass  # type: ignore[return]

    def check_hosts_status(self, request):  # type: ignore[override]
        return []

    def release_hosts(self, machine_ids, resource_mapping=None, request_id=""):  # type: ignore[override]
        pass

    def cancel_resource(self, resource_id, request_id):  # type: ignore[override]
        return {}

    @classmethod
    def get_example_templates(cls):
        return []


def _make_handler(config_port=None) -> _ConcreteHandler:
    """Build a _ConcreteHandler with all required deps mocked."""
    aws_client = MagicMock()
    logger = MagicMock()
    aws_ops = MagicMock()
    aws_ops.set_retry_method = MagicMock()
    aws_ops.set_pagination_method = MagicMock()
    launch_template_manager = MagicMock()

    return _ConcreteHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=launch_template_manager,
        config_port=config_port,
    )


def _make_config_port(failure_threshold: int, recovery_timeout: int):
    """Build a mock ConfigurationPort with circuit_breaker fields set."""
    cb = MagicMock()
    cb.failure_threshold = failure_threshold
    cb.recovery_timeout = recovery_timeout

    app_config = MagicMock()
    app_config.circuit_breaker = cb

    port = MagicMock()
    port.app_config = app_config
    return port


# ---------------------------------------------------------------------------
# Test 1: returns defaults when config_port is None
# ---------------------------------------------------------------------------


class TestGetCircuitBreakerConfigDefaults:
    def test_returns_defaults_when_no_config_port(self):
        handler = _make_handler(config_port=None)
        result = handler._get_circuit_breaker_config()

        assert result["failure_threshold"] == 5
        assert result["reset_timeout"] == 60
        assert result["half_open_timeout"] == 30


# ---------------------------------------------------------------------------
# Test 2: reads values from config_port.app_config.circuit_breaker
# ---------------------------------------------------------------------------


class TestGetCircuitBreakerConfigFromPort:
    def test_reads_failure_threshold_and_recovery_timeout(self):
        port = _make_config_port(failure_threshold=10, recovery_timeout=120)
        handler = _make_handler(config_port=port)
        result = handler._get_circuit_breaker_config()

        assert result["failure_threshold"] == 10
        assert result["reset_timeout"] == 120  # recovery_timeout → reset_timeout mapping
        assert result["half_open_timeout"] == 30  # still hardcoded

    def test_custom_values_are_reflected(self):
        port = _make_config_port(failure_threshold=3, recovery_timeout=45)
        handler = _make_handler(config_port=port)
        result = handler._get_circuit_breaker_config()

        assert result["failure_threshold"] == 3
        assert result["reset_timeout"] == 45


# ---------------------------------------------------------------------------
# Test 3: falls back gracefully when app_config raises AttributeError
# ---------------------------------------------------------------------------


class TestGetCircuitBreakerConfigFallback:
    def test_falls_back_on_attribute_error(self):
        # Use a plain object whose app_config property raises AttributeError
        class _BadPort:
            @property
            def app_config(self):
                raise AttributeError("no app_config")

        handler = _make_handler(config_port=_BadPort())
        result = handler._get_circuit_breaker_config()

        assert result["failure_threshold"] == 5
        assert result["reset_timeout"] == 60
        assert result["half_open_timeout"] == 30

    def test_falls_back_when_circuit_breaker_attr_missing(self):
        # app_config exists but has no circuit_breaker attribute
        class _AppConfigNoCB:
            pass

        class _PortNoCB:
            app_config = _AppConfigNoCB()

        handler = _make_handler(config_port=_PortNoCB())
        result = handler._get_circuit_breaker_config()

        assert result["failure_threshold"] == 5
        assert result["reset_timeout"] == 60


# ---------------------------------------------------------------------------
# Test 4: _get_retry_strategy_config("critical") uses config values
# ---------------------------------------------------------------------------


class TestRetryStrategyConfigCritical:
    def test_critical_uses_config_port_values(self):
        port = _make_config_port(failure_threshold=8, recovery_timeout=90)
        handler = _make_handler(config_port=port)
        config = handler._get_retry_strategy_config("critical", "ec2")

        assert config["strategy"] == "circuit_breaker"
        assert config["failure_threshold"] == 8
        assert config["reset_timeout"] == 90
        assert config["half_open_timeout"] == 30  # still hardcoded

    def test_critical_uses_defaults_when_no_config_port(self):
        handler = _make_handler(config_port=None)
        config = handler._get_retry_strategy_config("critical", "ec2")

        assert config["failure_threshold"] == 5
        assert config["reset_timeout"] == 60


# ---------------------------------------------------------------------------
# Test 5: non-critical paths are unaffected
# ---------------------------------------------------------------------------


class TestRetryStrategyConfigNonCritical:
    def test_standard_strategy_unchanged(self):
        port = _make_config_port(failure_threshold=99, recovery_timeout=999)
        handler = _make_handler(config_port=port)
        config = handler._get_retry_strategy_config("standard", "ec2")

        assert config["strategy"] == "exponential"
        assert "failure_threshold" not in config
        assert "reset_timeout" not in config

    def test_read_only_strategy_unchanged(self):
        port = _make_config_port(failure_threshold=99, recovery_timeout=999)
        handler = _make_handler(config_port=port)
        config = handler._get_retry_strategy_config("read_only", "ec2")

        assert config["strategy"] == "exponential"
        assert "failure_threshold" not in config
