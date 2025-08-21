"""Tests for native spec configuration schema."""

import pytest
from pydantic import ValidationError

from config.schemas.native_spec_schema import NativeSpecConfig


class TestNativeSpecConfig:
    """Test native spec configuration schema."""

    def test_default_values(self):
        """Test default configuration values."""
        config = NativeSpecConfig()
        assert config.enabled is False
        assert config.merge_mode == "extend"

    def test_valid_merge_modes(self):
        """Test valid merge mode values."""
        valid_modes = ["extend", "override", "none"]
        for mode in valid_modes:
            config = NativeSpecConfig(merge_mode=mode)
            assert config.merge_mode == mode

    def test_invalid_merge_mode(self):
        """Test invalid merge mode raises validation error."""
        with pytest.raises(ValidationError):
            NativeSpecConfig(merge_mode="invalid")

    def test_enabled_configuration(self):
        """Test enabled configuration."""
        config = NativeSpecConfig(enabled=True, merge_mode="override")
        assert config.enabled is True
        assert config.merge_mode == "override"
