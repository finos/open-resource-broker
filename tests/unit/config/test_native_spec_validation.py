"""Comprehensive tests for native spec validation."""

import pytest
from pydantic import ValidationError

from config.schemas.native_spec_schema import NativeSpecConfig
from providers.aws.domain.template.aggregate import AWSTemplate


class TestNativeSpecValidation:
    """Test native spec configuration validation."""

    def test_native_spec_config_defaults(self):
        """Test default native spec configuration."""
        config = NativeSpecConfig()

        assert config.enabled is False
        assert config.merge_mode == "merge"

    def test_native_spec_config_valid_merge_modes(self):
        """Test valid merge mode options."""
        valid_modes = ["merge", "replace"]

        for mode in valid_modes:
            config = NativeSpecConfig(enabled=True, merge_mode=mode)
            assert config.merge_mode == mode

    def test_native_spec_config_invalid_merge_mode(self):
        """Test invalid merge mode raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            NativeSpecConfig(enabled=True, merge_mode="invalid")

        assert "merge_mode" in str(exc_info.value)

    def test_native_spec_enabled_validation(self):
        """Test enabled field validation."""
        # Valid boolean values
        config1 = NativeSpecConfig(enabled=True)
        assert config1.enabled is True

        config2 = NativeSpecConfig(enabled=False)
        assert config2.enabled is False

    def test_aws_template_native_spec_fields(self):
        """Test AWS template native spec field validation."""
        # Valid template with inline specs
        template = AWSTemplate(
            template_id="test-template",
            image_id="ami-12345",
            instance_type="t3.micro",
            launch_template_spec={"LaunchTemplateName": "test-lt"},
            provider_api_spec={"Type": "instant"},
        )

        assert template.launch_template_spec is not None
        assert template.provider_api_spec is not None

    def test_aws_template_mutual_exclusion_launch_template(self):
        """Test mutual exclusion validation for launch template specs."""
        with pytest.raises(ValueError) as exc_info:
            AWSTemplate(
                template_id="test-template",
                image_id="ami-12345",
                instance_type="t3.micro",
                launch_template_spec={"LaunchTemplateName": "test-lt"},
                launch_template_spec_file="test-file.json",
            )

        assert "Cannot specify both launch_template_spec and launch_template_spec_file" in str(
            exc_info.value
        )

    def test_aws_template_mutual_exclusion_provider_api(self):
        """Test mutual exclusion validation for provider API specs."""
        with pytest.raises(ValueError) as exc_info:
            AWSTemplate(
                template_id="test-template",
                image_id="ami-12345",
                instance_type="t3.micro",
                provider_api_spec={"Type": "instant"},
                provider_api_spec_file="test-file.json",
            )

        assert "Cannot specify both provider_api_spec and provider_api_spec_file" in str(
            exc_info.value
        )

    def test_aws_template_valid_spec_combinations(self):
        """Test valid combinations of spec fields."""
        # Only inline specs
        template1 = AWSTemplate(
            template_id="test1",
            image_id="ami-12345",
            instance_type="t3.micro",
            launch_template_spec={"LaunchTemplateName": "test-lt"},
            provider_api_spec={"Type": "instant"},
        )
        assert template1.launch_template_spec is not None
        assert template1.provider_api_spec is not None

        # Only file-based specs
        template2 = AWSTemplate(
            template_id="test2",
            image_id="ami-12345",
            instance_type="t3.micro",
            launch_template_spec_file="lt-spec.json",
            provider_api_spec_file="api-spec.json",
        )
        assert template2.launch_template_spec_file is not None
        assert template2.provider_api_spec_file is not None

        # Mixed specs (different types)
        template3 = AWSTemplate(
            template_id="test3",
            image_id="ami-12345",
            instance_type="t3.micro",
            launch_template_spec={"LaunchTemplateName": "test-lt"},
            provider_api_spec_file="api-spec.json",
        )
        assert template3.launch_template_spec is not None
        assert template3.provider_api_spec_file is not None

    def test_aws_template_no_native_specs(self):
        """Test template without any native specs."""
        template = AWSTemplate(
            template_id="legacy-template", image_id="ami-12345", instance_type="t3.micro"
        )

        assert template.launch_template_spec is None
        assert template.launch_template_spec_file is None
        assert template.provider_api_spec is None
        assert template.provider_api_spec_file is None

    def test_native_spec_config_serialization(self):
        """Test native spec config serialization/deserialization."""
        config = NativeSpecConfig(enabled=True, merge_mode="replace")

        # Test dict conversion
        config_dict = config.model_dump()
        assert config_dict["enabled"] is True
        assert config_dict["merge_mode"] == "replace"

        # Test reconstruction
        new_config = NativeSpecConfig(**config_dict)
        assert new_config.enabled == config.enabled
        assert new_config.merge_mode == config.merge_mode

    @pytest.mark.parametrize("merge_mode", ["merge", "replace"])
    def test_all_merge_modes(self, merge_mode):
        """Test all valid merge modes."""
        config = NativeSpecConfig(enabled=True, merge_mode=merge_mode)
        assert config.merge_mode == merge_mode

    def test_native_spec_config_json_schema(self):
        """Test JSON schema generation for native spec config."""
        schema = NativeSpecConfig.model_json_schema()

        assert "enabled" in schema["properties"]
        assert "merge_mode" in schema["properties"]
        assert schema["properties"]["enabled"]["type"] == "boolean"
        assert "enum" in schema["properties"]["merge_mode"]
        assert set(schema["properties"]["merge_mode"]["enum"]) == {"merge", "replace"}

    def test_aws_template_spec_field_types(self):
        """Test native spec field type validation."""
        # Valid dict types
        template = AWSTemplate(
            template_id="test-template",
            image_id="ami-12345",
            instance_type="t3.micro",
            launch_template_spec={"key": "value"},
            provider_api_spec={"Type": "instant"},
        )

        assert isinstance(template.launch_template_spec, dict)
        assert isinstance(template.provider_api_spec, dict)

    def test_aws_template_spec_file_types(self):
        """Test native spec file field type validation."""
        template = AWSTemplate(
            template_id="test-template",
            image_id="ami-12345",
            instance_type="t3.micro",
            launch_template_spec_file="lt-spec.json",
            provider_api_spec_file="api-spec.json",
        )

        assert isinstance(template.launch_template_spec_file, str)
        assert isinstance(template.provider_api_spec_file, str)
