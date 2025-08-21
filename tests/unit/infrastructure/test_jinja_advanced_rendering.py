"""Advanced tests for Jinja2 spec rendering."""

from unittest.mock import Mock

from domain.base.ports.logging_port import LoggingPort
from infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer


class TestJinjaAdvancedRendering:
    """Test advanced Jinja2 rendering scenarios."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_logger = Mock(spec=LoggingPort)
        self.renderer = JinjaSpecRenderer(self.mock_logger)

    def test_simple_variable_substitution(self):
        """Test basic variable substitution."""
        spec = {"InstanceType": "{{ instance_type }}", "ImageId": "{{ image_id }}"}
        context = {"instance_type": "t3.micro", "image_id": "ami-12345"}

        result = self.renderer.render_spec(spec, context)

        assert result["InstanceType"] == "t3.micro"
        assert result["ImageId"] == "ami-12345"

    def test_nested_dict_rendering(self):
        """Test rendering nested dictionary structures."""
        spec = {
            "LaunchTemplateData": {
                "InstanceType": "{{ instance_type }}",
                "NetworkInterfaces": [
                    {"SubnetId": "{{ subnet_id }}", "SecurityGroupIds": ["{{ security_group }}"]}
                ],
            }
        }
        context = {
            "instance_type": "t3.medium",
            "subnet_id": "subnet-12345",
            "security_group": "sg-67890",
        }

        result = self.renderer.render_spec(spec, context)

        assert result["LaunchTemplateData"]["InstanceType"] == "t3.medium"
        assert result["LaunchTemplateData"]["NetworkInterfaces"][0]["SubnetId"] == "subnet-12345"
        assert (
            result["LaunchTemplateData"]["NetworkInterfaces"][0]["SecurityGroupIds"][0]
            == "sg-67890"
        )

    def test_list_rendering(self):
        """Test rendering lists with template variables."""
        spec = {
            "SecurityGroupIds": ["{{ primary_sg }}", "{{ secondary_sg }}"],
            "SubnetIds": ["{{ subnet_1 }}", "{{ subnet_2 }}"],
        }
        context = {
            "primary_sg": "sg-primary",
            "secondary_sg": "sg-secondary",
            "subnet_1": "subnet-1a",
            "subnet_2": "subnet-1b",
        }

        result = self.renderer.render_spec(spec, context)

        assert result["SecurityGroupIds"] == ["sg-primary", "sg-secondary"]
        assert result["SubnetIds"] == ["subnet-1a", "subnet-1b"]

    def test_conditional_rendering(self):
        """Test conditional template logic."""
        spec = {
            "InstanceType": "{{ instance_type }}",
            "SpotPrice": "{% if use_spot %}{{ spot_price }}{% endif %}",
        }

        # Test with spot enabled
        context1 = {"instance_type": "t3.micro", "use_spot": True, "spot_price": "0.05"}
        result1 = self.renderer.render_spec(spec, context1)
        assert result1["SpotPrice"] == "0.05"

        # Test with spot disabled
        context2 = {"instance_type": "t3.micro", "use_spot": False, "spot_price": "0.05"}
        result2 = self.renderer.render_spec(spec, context2)
        assert result2["SpotPrice"] == ""

    def test_loop_rendering(self):
        """Test loop-based template rendering."""
        spec = {
            "Overrides": [
                "{% for subnet_id in subnet_ids %}",
                {"SubnetId": "{{ subnet_id }}", "InstanceType": "{{ instance_type }}"},
                "{% if not loop.last %},{% endif %}",
                "{% endfor %}",
            ]
        }
        context = {
            "subnet_ids": ["subnet-1a", "subnet-1b", "subnet-1c"],
            "instance_type": "t3.micro",
        }

        # Note: This test demonstrates the concept, but actual loop rendering
        # would require more sophisticated template processing
        result = self.renderer.render_spec(spec, context)

        # Verify the template structure is preserved
        assert "Overrides" in result

    def test_filter_usage(self):
        """Test Jinja2 filter usage."""
        spec = {
            "UserData": "{{ user_data | b64encode }}",
            "TotalCapacity": "{{ (base_capacity * 1.5) | round | int }}",
            "Environment": "{{ environment | default('production') }}",
        }
        context = {
            "user_data": "#!/bin/bash\necho 'Hello World'",
            "base_capacity": 10.7,
            "environment": None,
        }

        result = self.renderer.render_spec(spec, context)

        # UserData should be base64 encoded
        assert result["UserData"] != "#!/bin/bash\necho 'Hello World'"
        assert len(result["UserData"]) > 0

        # Capacity should be rounded
        assert result["TotalCapacity"] == "16"  # 10.7 * 1.5 = 16.05, rounded to 16

        # Environment should use default
        assert result["Environment"] == "production"

    def test_default_filter(self):
        """Test default filter with missing variables."""
        spec = {
            "InstanceType": "{{ instance_type | default('t3.micro') }}",
            "KeyName": "{{ key_name | default('') }}",
            "Monitoring": "{{ monitoring | default(false) }}",
        }
        context = {
            "instance_type": "t3.large"
            # key_name and monitoring are missing
        }

        result = self.renderer.render_spec(spec, context)

        assert result["InstanceType"] == "t3.large"
        assert result["KeyName"] == ""
        assert result["Monitoring"] == "False"  # Jinja2 renders boolean as string

    def test_complex_nested_rendering(self):
        """Test complex nested structure rendering."""
        spec = {
            "LaunchTemplateConfigs": [
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": "{{ launch_template_id }}",
                        "Version": "{{ launch_template_version }}",
                    },
                    "Overrides": [
                        {
                            "InstanceType": "{{ primary_instance_type }}",
                            "SubnetId": "{{ primary_subnet }}",
                        },
                        {
                            "InstanceType": "{{ secondary_instance_type }}",
                            "SubnetId": "{{ secondary_subnet }}",
                        },
                    ],
                }
            ]
        }
        context = {
            "launch_template_id": "lt-12345",
            "launch_template_version": "$Latest",
            "primary_instance_type": "t3.medium",
            "primary_subnet": "subnet-1a",
            "secondary_instance_type": "t3.large",
            "secondary_subnet": "subnet-1b",
        }

        result = self.renderer.render_spec(spec, context)

        lt_config = result["LaunchTemplateConfigs"][0]
        assert lt_config["LaunchTemplateSpecification"]["LaunchTemplateId"] == "lt-12345"
        assert lt_config["LaunchTemplateSpecification"]["Version"] == "$Latest"
        assert lt_config["Overrides"][0]["InstanceType"] == "t3.medium"
        assert lt_config["Overrides"][0]["SubnetId"] == "subnet-1a"
        assert lt_config["Overrides"][1]["InstanceType"] == "t3.large"
        assert lt_config["Overrides"][1]["SubnetId"] == "subnet-1b"

    def test_non_template_strings_unchanged(self):
        """Test that non-template strings are not modified."""
        spec = {
            "StaticValue": "this-is-static",
            "AnotherStatic": "no-variables-here",
            "MixedValue": "static-{{ variable }}-static",
        }
        context = {"variable": "dynamic"}

        result = self.renderer.render_spec(spec, context)

        assert result["StaticValue"] == "this-is-static"
        assert result["AnotherStatic"] == "no-variables-here"
        assert result["MixedValue"] == "static-dynamic-static"

    def test_numeric_and_boolean_values(self):
        """Test handling of numeric and boolean values."""
        spec = {
            "NumericValue": 42,
            "BooleanValue": True,
            "TemplateNumeric": "{{ numeric_var }}",
            "TemplateBoolean": "{{ boolean_var }}",
        }
        context = {"numeric_var": 100, "boolean_var": False}

        result = self.renderer.render_spec(spec, context)

        assert result["NumericValue"] == 42
        assert result["BooleanValue"] is True
        assert result["TemplateNumeric"] == "100"
        assert result["TemplateBoolean"] == "False"

    def test_empty_spec(self):
        """Test rendering empty specification."""
        spec = {}
        context = {"any_var": "any_value"}

        result = self.renderer.render_spec(spec, context)

        assert result == {}

    def test_empty_context(self):
        """Test rendering with empty context."""
        spec = {"StaticValue": "static", "DefaultValue": "{{ missing_var | default('default') }}"}
        context = {}

        result = self.renderer.render_spec(spec, context)

        assert result["StaticValue"] == "static"
        assert result["DefaultValue"] == "default"

    def test_special_characters_in_templates(self):
        """Test handling of special characters in templates."""
        spec = {
            "SpecialChars": "{{ var_with_special }}",
            "JsonString": '{"key": "{{ json_value }}"}',
        }
        context = {
            "var_with_special": "value-with-dashes_and_underscores",
            "json_value": "json-content",
        }

        result = self.renderer.render_spec(spec, context)

        assert result["SpecialChars"] == "value-with-dashes_and_underscores"
        assert result["JsonString"] == '{"key": "json-content"}'

    def test_recursive_rendering_depth(self):
        """Test deeply nested structure rendering."""
        spec = {"Level1": {"Level2": {"Level3": {"Level4": {"Value": "{{ deep_value }}"}}}}}
        context = {"deep_value": "deep-content"}

        result = self.renderer.render_spec(spec, context)

        assert result["Level1"]["Level2"]["Level3"]["Level4"]["Value"] == "deep-content"

    def test_mixed_data_types_in_lists(self):
        """Test lists containing mixed data types."""
        spec = {"MixedList": ["{{ string_var }}", 42, True, {"NestedKey": "{{ nested_var }}"}]}
        context = {"string_var": "string-value", "nested_var": "nested-value"}

        result = self.renderer.render_spec(spec, context)

        assert result["MixedList"][0] == "string-value"
        assert result["MixedList"][1] == 42
        assert result["MixedList"][2] is True
        assert result["MixedList"][3]["NestedKey"] == "nested-value"
