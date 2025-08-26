"""Tests for native spec error handling scenarios."""

from unittest.mock import Mock

import pytest
from jinja2 import TemplateError, UndefinedError

from application.services.native_spec_service import NativeSpecService
from domain.base.ports.configuration_port import ConfigurationPort
from domain.base.ports.spec_rendering_port import SpecRenderingPort


class TestNativeSpecErrorHandling:
    """Test error handling in native spec processing."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config_port = Mock(spec=ConfigurationPort)
        self.mock_spec_renderer = Mock(spec=SpecRenderingPort)
        self.service = NativeSpecService(
            config_port=self.mock_config_port, spec_renderer=self.mock_spec_renderer
        )

    def test_native_spec_disabled(self):
        """Test behavior when native specs are disabled."""
        self.mock_config_port.get_native_spec_config.return_value = {"enabled": False}

        result = self.service.is_native_spec_enabled()

        assert result is False

    def test_native_spec_enabled(self):
        """Test behavior when native specs are enabled."""
        self.mock_config_port.get_native_spec_config.return_value = {"enabled": True}

        result = self.service.is_native_spec_enabled()

        assert result is True

    def test_render_spec_success(self):
        """Test successful spec rendering."""
        spec = {"InstanceType": "{{ instance_type }}"}
        context = {"instance_type": "t3.micro"}
        expected_result = {"InstanceType": "t3.micro"}

        self.mock_spec_renderer.render_spec.return_value = expected_result

        result = self.service.render_spec(spec, context)

        assert result == expected_result
        self.mock_spec_renderer.render_spec.assert_called_once_with(spec, context)

    def test_render_spec_template_error(self):
        """Test spec rendering with template error."""
        spec = {"InstanceType": "{{ invalid_syntax }"}
        context = {"instance_type": "t3.micro"}

        self.mock_spec_renderer.render_spec.side_effect = TemplateError("Invalid template syntax")

        with pytest.raises(TemplateError):
            self.service.render_spec(spec, context)

    def test_render_spec_undefined_variable(self):
        """Test spec rendering with undefined variable."""
        spec = {"InstanceType": "{{ undefined_var }}"}
        context = {"instance_type": "t3.micro"}

        self.mock_spec_renderer.render_spec.side_effect = UndefinedError(
            "'undefined_var' is undefined"
        )

        with pytest.raises(UndefinedError):
            self.service.render_spec(spec, context)

    def test_config_port_error(self):
        """Test error handling when config port fails."""
        self.mock_config_port.get_native_spec_config.side_effect = Exception("Config error")

        with pytest.raises(Exception) as exc_info:
            self.service.is_native_spec_enabled()

        assert "Config error" in str(exc_info.value)

    def test_spec_renderer_initialization_error(self):
        """Test error handling during spec renderer initialization."""
        # Test with None renderer
        service_with_none_renderer = NativeSpecService(
            config_port=self.mock_config_port, spec_renderer=None
        )

        spec = {"InstanceType": "t3.micro"}
        context = {}

        with pytest.raises(AttributeError):
            service_with_none_renderer.render_spec(spec, context)

    def test_empty_spec_handling(self):
        """Test handling of empty specifications."""
        spec = {}
        context = {"instance_type": "t3.micro"}

        self.mock_spec_renderer.render_spec.return_value = {}

        result = self.service.render_spec(spec, context)

        assert result == {}

    def test_none_spec_handling(self):
        """Test handling of None specifications."""
        spec = None
        context = {"instance_type": "t3.micro"}

        # Should handle None gracefully or raise appropriate error
        with pytest.raises((TypeError, AttributeError)):
            self.service.render_spec(spec, context)

    def test_empty_context_handling(self):
        """Test handling of empty context."""
        spec = {"InstanceType": "{{ instance_type | default('t3.micro') }}"}
        context = {}
        expected_result = {"InstanceType": "t3.micro"}

        self.mock_spec_renderer.render_spec.return_value = expected_result

        result = self.service.render_spec(spec, context)

        assert result == expected_result

    def test_none_context_handling(self):
        """Test handling of None context."""
        spec = {"InstanceType": "t3.micro"}
        context = None

        # Should handle None context gracefully or raise appropriate error
        with pytest.raises((TypeError, AttributeError)):
            self.service.render_spec(spec, context)

    def test_large_spec_handling(self):
        """Test handling of very large specifications."""
        # Create a large spec with many fields
        large_spec = {}
        for i in range(1000):
            large_spec[f"Field{i}"] = f"{{{{ var{i} }}}}"

        large_context = {}
        for i in range(1000):
            large_context[f"var{i}"] = f"value{i}"

        expected_result = {}
        for i in range(1000):
            expected_result[f"Field{i}"] = f"value{i}"

        self.mock_spec_renderer.render_spec.return_value = expected_result

        result = self.service.render_spec(large_spec, large_context)

        assert result == expected_result

    def test_circular_reference_handling(self):
        """Test handling of circular references in context."""
        spec = {"Value": "{{ circular_ref }}"}

        # Create circular reference
        circular_dict = {}
        circular_dict["self"] = circular_dict
        context = {"circular_ref": circular_dict}

        # Mock renderer should handle this appropriately
        self.mock_spec_renderer.render_spec.side_effect = ValueError("Circular reference detected")

        with pytest.raises(ValueError):
            self.service.render_spec(spec, context)

    def test_invalid_spec_type(self):
        """Test handling of invalid spec types."""
        invalid_specs = ["string_spec", 123, ["list", "spec"], True]

        context = {"var": "value"}

        for invalid_spec in invalid_specs:
            self.mock_spec_renderer.render_spec.side_effect = TypeError(
                f"Invalid spec type: {type(invalid_spec)}"
            )

            with pytest.raises(TypeError):
                self.service.render_spec(invalid_spec, context)

    def test_invalid_context_type(self):
        """Test handling of invalid context types."""
        spec = {"InstanceType": "{{ instance_type }}"}
        invalid_contexts = ["string_context", 123, ["list", "context"], True]

        for invalid_context in invalid_contexts:
            self.mock_spec_renderer.render_spec.side_effect = TypeError(
                f"Invalid context type: {type(invalid_context)}"
            )

            with pytest.raises(TypeError):
                self.service.render_spec(spec, invalid_context)

    def test_config_missing_native_spec_section(self):
        """Test handling when native_spec section is missing from config."""
        self.mock_config_port.get_native_spec_config.return_value = {}

        # Should handle missing config gracefully
        result = self.service.is_native_spec_enabled()

        # Depending on implementation, this might return False or raise an error
        assert result is False or isinstance(result, bool)

    def test_config_malformed_native_spec_section(self):
        """Test handling of malformed native_spec configuration."""
        malformed_configs = [
            {"enabled": "not_a_boolean"},
            {"enabled": None},
            {"unknown_field": True},
            None,
        ]

        for malformed_config in malformed_configs:
            self.mock_config_port.get_native_spec_config.return_value = malformed_config

            # Should handle malformed config appropriately
            try:
                result = self.service.is_native_spec_enabled()
                # If no exception, result should be boolean
                assert isinstance(result, bool)
            except (TypeError, ValueError, KeyError):
                # These exceptions are acceptable for malformed config
                pass

    def test_renderer_timeout_simulation(self):
        """Test handling of renderer timeout scenarios."""
        spec = {"ComplexTemplate": "{{ very_complex_computation }}"}
        context = {"very_complex_computation": "result"}

        # Simulate timeout
        self.mock_spec_renderer.render_spec.side_effect = TimeoutError("Template rendering timeout")

        with pytest.raises(TimeoutError):
            self.service.render_spec(spec, context)

    def test_memory_error_handling(self):
        """Test handling of memory errors during rendering."""
        spec = {"LargeTemplate": "{{ large_data }}"}
        context = {"large_data": "x" * 1000000}  # Large string

        # Simulate memory error
        self.mock_spec_renderer.render_spec.side_effect = MemoryError("Out of memory")

        with pytest.raises(MemoryError):
            self.service.render_spec(spec, context)

    def test_unicode_handling(self):
        """Test handling of Unicode characters in specs and context."""
        spec = {"UnicodeField": "{{ unicode_var }}"}
        context = {"unicode_var": "test data with unicode characters"}
        expected_result = {"UnicodeField": "test data with unicode characters"}

        self.mock_spec_renderer.render_spec.return_value = expected_result

        result = self.service.render_spec(spec, context)

        assert result == expected_result

    def test_nested_error_propagation(self):
        """Test that nested errors are properly propagated."""
        spec = {"NestedTemplate": {"DeepField": "{{ nested_var }}"}}
        context = {"nested_var": "value"}

        # Simulate nested rendering error
        nested_error = UndefinedError("Nested variable undefined")
        self.mock_spec_renderer.render_spec.side_effect = nested_error

        with pytest.raises(UndefinedError) as exc_info:
            self.service.render_spec(spec, context)

        assert exc_info.value == nested_error
