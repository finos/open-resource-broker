"""Tests for multi-provider domain model updates."""

import pytest

from src.domain.request.aggregate import Request
from src.domain.request.value_objects import RequestId, RequestType
from src.domain.template.aggregate import Template


class TestTemplateMultiProviderFields:
    """Test suite for Template aggregate multi-provider fields."""

    def test_template_with_all_provider_fields(self):
        """Test template creation with all provider fields."""
        template = Template(
            template_id="multi-provider-test",
            provider_type="aws",
            provider_name="aws-us-east-1",
            provider_api="EC2Fleet",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=5,
        )

        assert template.template_id == "multi-provider-test"
        assert template.provider_type == "aws"
        assert template.provider_name == "aws-us-east-1"
        assert template.provider_api == "EC2Fleet"
        assert template.image_id == "ami-12345"
        assert template.subnet_ids == ["subnet-123"]
        assert template.max_instances == 5

    def test_template_with_partial_provider_fields(self):
        """Test template creation with partial provider fields."""
        template = Template(
            template_id="partial-provider-test",
            provider_type="aws",
            provider_api="SpotFleet",
            image_id="ami-67890",
            subnet_ids=["subnet-456"],
            max_instances=3,
        )

        assert template.provider_type == "aws"
        assert template.provider_name is None
        assert template.provider_api == "SpotFleet"

    def test_template_without_provider_fields(self):
        """Test template creation without provider fields (backward compatibility)."""
        template = Template(
            template_id="legacy-test",
            image_id="ami-legacy",
            subnet_ids=["subnet-legacy"],
            max_instances=1,
        )

        assert template.provider_type is None
        assert template.provider_name is None
        assert template.provider_api is None

    def test_template_provider_type_extraction_from_name(self):
        """Test automatic provider type extraction from provider name."""
        template = Template(
            template_id="extraction-test",
            provider_name="aws-us-east-1",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=2,
        )

        # Provider type should be extracted from provider name
        assert template.provider_type == "aws"
        assert template.provider_name == "aws-us-east-1"

    def test_template_provider_type_extraction_provider1(self):
        """Test provider type extraction for Provider1 provider."""
        template = Template(
            template_id="provider1-test",
            provider_name="provider1-west-us",
            image_id="image-12345",
            subnet_ids=["subnet-123"],
            max_instances=2,
        )

        assert template.provider_type == "provider1"
        assert template.provider_name == "provider1-west-us"

    def test_template_provider_type_extraction_single_word(self):
        """Test provider type extraction for single-word provider name."""
        template = Template(
            template_id="single-word-test",
            provider_name="aws",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        assert template.provider_type == "aws"
        assert template.provider_name == "aws"

    def test_template_provider_name_validation_valid(self):
        """Test provider name validation with valid characters."""
        template = Template(
            template_id="validation-test",
            provider_name="aws-us-east-1_primary",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        assert template.provider_name == "aws-us-east-1_primary"

    def test_template_provider_name_validation_invalid(self):
        """Test provider name validation with invalid characters."""
        with pytest.raises(ValueError, match="provider_name must contain only alphanumeric"):
            Template(
                template_id="invalid-name-test",
                provider_name="aws@us-east-1",  # @ is invalid
                image_id="ami-12345",
                subnet_ids=["subnet-123"],
                max_instances=1,
            )

    def test_template_provider_type_validation_valid(self):
        """Test provider type validation with valid format."""
        template = Template(
            template_id="type-validation-test",
            provider_type="aws123",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        assert template.provider_type == "aws123"

    def test_template_provider_type_validation_invalid(self):
        """Test provider type validation with invalid format."""
        with pytest.raises(ValueError, match="provider_type must be lowercase alphanumeric"):
            Template(
                template_id="invalid-type-test",
                provider_type="AWS-Primary",  # Uppercase and hyphen invalid
                image_id="ami-12345",
                subnet_ids=["subnet-123"],
                max_instances=1,
            )

    def test_template_existing_validation_still_works(self):
        """Test that existing template validation still works."""
        # Test max_instances validation
        with pytest.raises(ValueError, match="max_instances must be greater than 0"):
            Template(
                template_id="validation-test",
                image_id="ami-12345",
                subnet_ids=["subnet-123"],
                max_instances=0,
            )

        # Test image_id validation
        with pytest.raises(ValueError, match="image_id is required"):
            Template(
                template_id="validation-test",
                subnet_ids=["subnet-123"],
                max_instances=1,
            )

        # Test subnet_ids validation
        with pytest.raises(ValueError, match="At least one subnet_id is required"):
            Template(
                template_id="validation-test",
                image_id="ami-12345",
                subnet_ids=[],
                max_instances=1,
            )


class TestRequestMultiProviderFields:
    """Test suite for Request aggregate multi-provider fields."""

    def test_request_creation_with_provider_instance(self):
        """Test request creation with provider instance field."""
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="test-template",
            machine_count=3,
            provider_type="aws",
            provider_instance="aws-us-east-1",
        )

        assert request.provider_type == "aws"
        assert request.provider_instance == "aws-us-east-1"
        assert request.template_id == "test-template"
        assert request.requested_count == 3
        assert isinstance(request.request_id, RequestId)

    def test_request_creation_without_provider_instance(self):
        """Test request creation without provider instance (backward compatibility)."""
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="legacy-template",
            machine_count=2,
            provider_type="aws",
        )

        assert request.provider_type == "aws"
        assert request.provider_instance is None
        assert request.template_id == "legacy-template"
        assert request.requested_count == 2

    def test_request_creation_with_metadata(self):
        """Test request creation with provider metadata."""
        metadata = {
            "provider_selection_reason": "Load balanced selection",
            "provider_confidence": 0.9,
            "custom_field": "custom_value",
        }

        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="metadata-test",
            machine_count=1,
            provider_type="aws",
            provider_instance="aws-us-west-2",
            metadata=metadata,
        )

        assert request.provider_instance == "aws-us-west-2"
        assert request.metadata["provider_selection_reason"] == "Load balanced selection"
        assert request.metadata["provider_confidence"] == 0.9
        assert request.metadata["custom_field"] == "custom_value"

    def test_request_serialization_with_provider_fields(self):
        """Test request serialization includes provider fields."""
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="serialization-test",
            machine_count=2,
            provider_type="aws",
            provider_instance="aws-eu-west-1",
        )

        # Test that the request can be serialized (basic check)
        assert hasattr(request, "provider_type")
        assert hasattr(request, "provider_instance")
        assert request.provider_type == "aws"
        assert request.provider_instance == "aws-eu-west-1"


class TestTemplateAdditionalMultiProviderFields:
    """Test suite for additional Template multi-provider fields."""

    def test_template_with_additional_provider_fields(self):
        """Test Template creation with additional provider fields."""
        template = Template(
            template_id="dto-test",
            provider_type="aws",
            provider_name="aws-us-east-1",
            provider_api="EC2Fleet",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=4,
        )

        assert template.template_id == "dto-test"
        assert template.provider_type == "aws"
        assert template.provider_name == "aws-us-east-1"
        assert template.provider_api == "EC2Fleet"
        assert template.image_id == "ami-12345"
        assert template.subnet_ids == ["subnet-123"]
        assert template.max_instances == 4

    def test_template_without_additional_provider_fields(self):
        """Test Template creation without additional provider fields."""
        template = Template(
            template_id="dto-legacy-test",
            image_id="ami-legacy",
            subnet_ids=["subnet-legacy"],
            max_instances=1,
        )

        assert template.provider_type is None
        assert template.provider_name is None
        assert template.provider_api is None

    def test_template_additional_defaults(self):
        """Test Template additional default values."""
        template = Template(
            template_id="defaults-test",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        # Check that provider fields default to None
        assert template.provider_type is None
        assert template.provider_name is None
        assert template.provider_api is None

        # Check other defaults
        assert template.is_active is True


class TestMultiProviderBackwardCompatibility:
    """Test suite for backward compatibility of multi-provider changes."""

    def test_existing_template_creation_still_works(self):
        """Test that existing template creation patterns still work."""
        # This is how templates were created before multi-provider
        template = Template(
            template_id="backward-compat-test",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=2,
            instance_type="t2.micro",
            key_name="my-key",
            security_group_ids=["sg-123"],
        )

        assert template.template_id == "backward-compat-test"
        assert template.image_id == "ami-12345"
        assert template.max_instances == 2
        # New fields should be None
        assert template.provider_type is None
        assert template.provider_name is None
        assert template.provider_api is None

    def test_existing_request_creation_still_works(self):
        """Test that existing request creation patterns still work."""
        # This is how requests were created before multi-provider
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="backward-compat-request",
            machine_count=1,
            provider_type="aws",  # This was the only provider field before
        )

        assert request.template_id == "backward-compat-request"
        assert request.requested_count == 1
        assert request.provider_type == "aws"
        # New field should be None
        assert request.provider_instance is None
