"""Tests for native spec service."""

from unittest.mock import Mock

import pytest

from application.services.native_spec_service import NativeSpecService


class TestNativeSpecService:
    """Test native spec service."""

    @pytest.fixture
    def config_port(self):
        """Mock configuration port."""
        return Mock()

    @pytest.fixture
    def spec_renderer(self):
        """Mock spec renderer."""
        return Mock()

    @pytest.fixture
    def service(self, config_port, spec_renderer):
        """Create service instance."""
        return NativeSpecService(config_port, spec_renderer)

    def test_is_native_spec_enabled_true(self, service, config_port):
        """Test native spec enabled check returns true."""
        config_port.get_native_spec_config.return_value = {"enabled": True}

        result = service.is_native_spec_enabled()

        assert result is True

    def test_is_native_spec_enabled_false(self, service, config_port):
        """Test native spec enabled check returns false."""
        config_port.get_native_spec_config.return_value = {"enabled": False}

        result = service.is_native_spec_enabled()

        assert result is False

    def test_render_spec_delegates_to_renderer(self, service, spec_renderer):
        """Test render spec delegates to spec renderer."""
        spec = {"name": "test"}
        context = {"instance_type": "t2.micro"}
        expected_result = {"name": "test-rendered"}

        spec_renderer.render_spec.return_value = expected_result

        result = service.render_spec(spec, context)

        spec_renderer.render_spec.assert_called_once_with(spec, context)
        assert result == expected_result
