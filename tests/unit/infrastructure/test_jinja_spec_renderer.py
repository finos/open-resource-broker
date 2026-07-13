"""Tests for Jinja spec renderer."""

from unittest.mock import Mock

import pytest

from orb.infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer


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
        spec = {
            "config": {
                "instance": "{{ instance_type }}",
                "count": "{{ requested_count }}",
            }
        }
        context = {"instance_type": "t2.micro", "requested_count": 5}

        result = renderer.render_spec(spec, context)

        assert result == {"config": {"instance": "t2.micro", "count": "5"}}

    def test_render_list_values(self, renderer):
        """Test rendering list values."""
        spec = {"tags": ["Name={{ instance_name }}", "Type={{ instance_type }}"]}
        context = {"instance_name": "test-instance", "instance_type": "t2.micro"}

        result = renderer.render_spec(spec, context)

        assert result == {"tags": ["Name=test-instance", "Type=t2.micro"]}

    def test_render_spec_from_file_with_jinja(self, renderer, tmp_path):
        """Test rendering spec from file with Jinja2 templating."""
        # Create temporary template file
        template_file = tmp_path / "test_template.json"
        template_content = """
{
  "name": "{{ fleet_name }}",
  "capacity": {{ target_capacity }},
  "tags": [
    {% for tag in base_tags %}
    {"Key": "{{ tag.key }}", "Value": "{{ tag.value }}"}{% if not loop.last %},{% endif %}
    {% endfor %}
  ]
}
        """.strip()
        template_file.write_text(template_content)

        context = {
            "fleet_name": "test-fleet",
            "target_capacity": 5,
            "base_tags": [
                {"key": "RequestId", "value": "req-123"},
                {"key": "TemplateId", "value": "template-456"},
            ],
        }

        result = renderer.render_spec_from_file(str(template_file), context)

        assert result["name"] == "test-fleet"
        assert result["capacity"] == 5
        assert len(result["tags"]) == 2
        assert result["tags"][0]["Key"] == "RequestId"
        assert result["tags"][0]["Value"] == "req-123"

    def test_render_spec_from_file_static_json(self, renderer, tmp_path):
        """Test rendering spec from static JSON file."""
        # Create temporary static JSON file
        json_file = tmp_path / "static.json"
        json_content = """
{
  "name": "static-fleet",
  "capacity": 10,
  "type": "instant"
}
        """.strip()
        json_file.write_text(json_content)

        context = {"unused": "value"}

        result = renderer.render_spec_from_file(str(json_file), context)

        assert result["name"] == "static-fleet"
        assert result["capacity"] == 10
        assert result["type"] == "instant"

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

    # ------------------------------------------------------------------
    # YAML support in render_spec_from_file
    # ------------------------------------------------------------------

    def test_render_spec_from_yaml_file(self, renderer, tmp_path):
        """render_spec_from_file parses .yaml files via yaml.safe_load."""
        yaml_file = tmp_path / "manifest.yaml"
        yaml_file.write_text(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: {{ resource_name }}\n"
            "  namespace: {{ namespace }}\n"
        )
        ctx = {"resource_name": "orb-test", "namespace": "default"}
        result = renderer.render_spec_from_file(str(yaml_file), ctx)
        assert result["apiVersion"] == "apps/v1"
        assert result["kind"] == "Deployment"
        assert result["metadata"]["name"] == "orb-test"
        assert result["metadata"]["namespace"] == "default"

    def test_render_spec_from_yml_file(self, renderer, tmp_path):
        """render_spec_from_file treats .yml extension the same as .yaml."""
        yml_file = tmp_path / "manifest.yml"
        yml_file.write_text("kind: Pod\napiVersion: v1\nspec:\n  replicas: {{ replicas }}\n")
        result = renderer.render_spec_from_file(str(yml_file), {"replicas": 3})
        assert result["kind"] == "Pod"
        assert result["spec"]["replicas"] == 3

    def test_render_spec_from_json_file_still_works(self, renderer, tmp_path):
        """render_spec_from_file keeps the JSON-parse path for .json files."""
        json_file = tmp_path / "spec.json"
        json_file.write_text('{"apiVersion": "v1", "kind": "{{ kind }}", "replicas": 2}')
        result = renderer.render_spec_from_file(str(json_file), {"kind": "Pod"})
        assert result["apiVersion"] == "v1"
        assert result["kind"] == "Pod"
        assert result["replicas"] == 2

    def test_yaml_jinja_variables_substituted(self, renderer, tmp_path):
        """Jinja variables inside a YAML file are substituted before YAML parse."""
        yaml_file = tmp_path / "tpl.yaml"
        yaml_file.write_text("labels:\n  request-id: '{{ request_id }}'\n  count: '{{ count }}'\n")
        result = renderer.render_spec_from_file(
            str(yaml_file), {"request_id": "req-abc", "count": 5}
        )
        assert result["labels"]["request-id"] == "req-abc"
        assert result["labels"]["count"] == "5"
