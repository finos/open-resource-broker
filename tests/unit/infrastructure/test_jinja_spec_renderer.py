"""Tests for Jinja spec renderer."""

from unittest.mock import Mock

import pytest

from infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer


class TestJinjaSpecRenderer:
    """Test Jinja spec renderer."""

    @pytest.fixture
    def logger(self):
        """Mock logger."""
        return Mock()

    @pytest.fixture
    def renderer(self, logger):
        """Create renderer instance."""
        return JinjaSpecRenderer(logger)

    def test_render_simple_template(self, renderer):
        """Test rendering simple template variables."""
        spec = {"name": "test-{{ instance_type }}"}
        context = {"instance_type": "t2.micro"}

        result = renderer.render_spec(spec, context)

        assert result == {"name": "test-t2.micro"}

    def test_render_nested_dict(self, renderer):
        """Test rendering nested dictionary structures."""
        spec = {"config": {"instance": "{{ instance_type }}", "count": "{{ requested_count }}"}}
        context = {"instance_type": "t2.micro", "requested_count": 5}

        result = renderer.render_spec(spec, context)

        assert result == {"config": {"instance": "t2.micro", "count": "5"}}

    def test_render_list_values(self, renderer):
        """Test rendering list values."""
        spec = {"tags": ["Name={{ instance_name }}", "Type={{ instance_type }}"]}
        context = {"instance_name": "test-instance", "instance_type": "t2.micro"}

        result = renderer.render_spec(spec, context)

        assert result == {"tags": ["Name=test-instance", "Type=t2.micro"]}

    def test_render_non_template_strings(self, renderer):
        """Test that non-template strings are left unchanged."""
        spec = {"name": "static-value", "count": 42}
        context = {"instance_type": "t2.micro"}

        result = renderer.render_spec(spec, context)

        assert result == {"name": "static-value", "count": 42}

    def test_render_empty_spec(self, renderer):
        """Test rendering empty spec."""
        spec = {}
        context = {"instance_type": "t2.micro"}

        result = renderer.render_spec(spec, context)

        assert result == {}
