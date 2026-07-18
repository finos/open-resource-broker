"""Tests for network_utils.py utilities."""

import pytest

from orb.infrastructure.utilities.network_utils import (
    LONG_TIMEOUT,
    QUICK_TIMEOUT,
    STANDARD_TIMEOUT,
    TimeoutConfig,
    get_requests_timeout,
)


@pytest.mark.unit
class TestTimeoutConfig:
    """Tests for TimeoutConfig."""

    def test_timeout_config_defaults(self):
        config = TimeoutConfig()
        assert config.connect > 0
        assert config.read > 0
        assert config.total >= config.connect + config.read

    def test_timeout_config_custom_values(self):
        config = TimeoutConfig(connect=3.0, read=7.0)
        assert config.connect == 3.0
        assert config.read == 7.0
        assert config.total == 10.0

    def test_timeout_config_explicit_total(self):
        config = TimeoutConfig(connect=2.0, read=8.0, total=15.0)
        assert config.total == 15.0

    def test_timeout_config_total_defaults_to_sum(self):
        config = TimeoutConfig(connect=4.0, read=6.0)
        assert config.total == 10.0

    def test_as_tuple_returns_connect_read(self):
        config = TimeoutConfig(connect=5.0, read=10.0)
        t = config.as_tuple()
        assert t == (5.0, 10.0)
        assert isinstance(t, tuple)

    def test_as_dict_contains_expected_keys(self):
        config = TimeoutConfig(connect=2.0, read=3.0)
        d = config.as_dict()
        assert "connect" in d
        assert "read" in d
        assert "total" in d
        assert d["connect"] == 2.0
        assert d["read"] == 3.0


@pytest.mark.unit
class TestPredefinedTimeouts:
    """Tests for predefined TimeoutConfig instances."""

    def test_quick_timeout_is_short(self):
        assert QUICK_TIMEOUT.connect <= 10
        assert QUICK_TIMEOUT.read <= 30

    def test_standard_timeout_exists(self):
        assert STANDARD_TIMEOUT.connect > 0
        assert STANDARD_TIMEOUT.read > 0

    def test_long_timeout_longer_than_standard(self):
        assert LONG_TIMEOUT.total >= STANDARD_TIMEOUT.total


@pytest.mark.unit
class TestGetRequestsTimeout:
    """Tests for get_requests_timeout."""

    def test_get_requests_timeout_default_returns_tuple(self):
        result = get_requests_timeout()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_get_requests_timeout_uses_standard_when_none(self):
        result = get_requests_timeout(None)
        expected = STANDARD_TIMEOUT.as_tuple()
        assert result == expected

    def test_get_requests_timeout_uses_custom_config(self):
        custom = TimeoutConfig(connect=1.0, read=2.0)
        result = get_requests_timeout(custom)
        assert result == (1.0, 2.0)

    def test_get_requests_timeout_with_quick_timeout(self):
        result = get_requests_timeout(QUICK_TIMEOUT)
        assert result == QUICK_TIMEOUT.as_tuple()
