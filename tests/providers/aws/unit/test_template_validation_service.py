"""Unit tests for AWSTemplateValidationService.

Covers validate_template and _validate_aws_template across happy, sad,
and edge-case paths.
"""

from unittest.mock import MagicMock

import pytest

from orb.providers.aws.services.template_validation_service import AWSTemplateValidationService
from orb.providers.base.strategy import ProviderOperation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service():
    logger = MagicMock()
    return AWSTemplateValidationService(logger=logger)


def _make_operation(params=None):
    op = MagicMock(spec=ProviderOperation)
    op.parameters = params or {}
    return op


# ---------------------------------------------------------------------------
# _validate_aws_template
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateAwsTemplate:
    def test_valid_template_config(self):
        svc = _make_service()
        config = {"image_id": "ami-0abc123", "instance_type": "t3.micro"}
        result = svc._validate_aws_template(config)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_missing_image_id_is_error(self):
        svc = _make_service()
        config = {"instance_type": "t3.micro"}
        result = svc._validate_aws_template(config)
        assert result["valid"] is False
        assert any("image_id" in e or "image_id" in e.lower() for e in result["errors"])

    def test_launch_template_id_satisfies_image_id_requirement(self):
        svc = _make_service()
        config = {"launch_template_id": "lt-001", "instance_type": "t3.micro"}
        result = svc._validate_aws_template(config)
        assert result["valid"] is True

    def test_missing_instance_config_is_error(self):
        svc = _make_service()
        config = {"image_id": "ami-0abc123"}
        result = svc._validate_aws_template(config)
        assert result["valid"] is False
        assert any("instance" in e.lower() for e in result["errors"])

    def test_instance_types_satisfies_instance_requirement(self):
        svc = _make_service()
        config = {"image_id": "ami-0abc123", "instance_types": ["t3.micro", "t3.small"]}
        result = svc._validate_aws_template(config)
        assert result["valid"] is True

    def test_abis_instance_requirements_satisfies_instance_requirement(self):
        svc = _make_service()
        config = {
            "image_id": "ami-0abc123",
            "abis_instance_requirements": {"VCpuCount": {"Min": 2}},
        }
        result = svc._validate_aws_template(config)
        assert result["valid"] is True

    def test_invalid_ami_format_is_error(self):
        svc = _make_service()
        config = {"image_id": "not-an-ami", "instance_type": "t3.micro"}
        result = svc._validate_aws_template(config)
        assert result["valid"] is False
        assert any("AMI" in e for e in result["errors"])

    def test_ssm_path_ami_is_valid(self):
        svc = _make_service()
        config = {
            "image_id": "/aws/service/ami-amazon-linux-latest/amzn2",
            "instance_type": "t3.micro",
        }
        result = svc._validate_aws_template(config)
        assert result["valid"] is True

    def test_uncommon_instance_type_produces_warning(self):
        svc = _make_service()
        config = {"image_id": "ami-0abc123", "instance_type": "x2idn.32xlarge"}
        result = svc._validate_aws_template(config)
        assert result["valid"] is True
        assert any("x2idn" in w for w in result["warnings"])

    def test_validated_fields_lists_all_keys(self):
        svc = _make_service()
        config = {"image_id": "ami-0abc123", "instance_type": "t3.micro", "key_name": "my-key"}
        result = svc._validate_aws_template(config)
        assert set(result["validated_fields"]) == {"image_id", "instance_type", "key_name"}

    def test_both_image_and_instance_missing_multiple_errors(self):
        svc = _make_service()
        config = {}
        result = svc._validate_aws_template(config)
        assert result["valid"] is False
        assert len(result["errors"]) >= 2


# ---------------------------------------------------------------------------
# validate_template (ProviderOperation path)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateTemplateOperation:
    def test_valid_config_returns_success_result(self):
        svc = _make_service()
        op = _make_operation(
            {"template_config": {"image_id": "ami-0abc123", "instance_type": "t3.micro"}}
        )
        result = svc.validate_template(op)
        assert result.success is True
        assert result.data["valid"] is True

    def test_missing_template_config_returns_error(self):
        svc = _make_service()
        op = _make_operation({})  # no template_config key
        result = svc.validate_template(op)
        assert result.success is False
        assert result.error_code == "MISSING_TEMPLATE_CONFIG"

    def test_empty_template_config_returns_error(self):
        svc = _make_service()
        op = _make_operation({"template_config": {}})
        result = svc.validate_template(op)
        # empty config counts as missing
        assert result.success is False

    def test_exception_returns_error_result(self):
        svc = _make_service()
        # Patch _validate_aws_template to raise
        svc._validate_aws_template = MagicMock(side_effect=RuntimeError("validation exploded"))
        op = _make_operation({"template_config": {"image_id": "ami-0abc123"}})
        result = svc.validate_template(op)
        assert result.success is False
        assert result.error_code == "VALIDATE_TEMPLATE_ERROR"


@pytest.mark.unit
class TestNoHardcodedTemplateCatalog:
    """Template listing is served by the template configuration, not this service."""

    def test_service_has_no_sample_template_catalog(self):
        svc = _make_service()
        assert not hasattr(svc, "get_available_templates")
        assert not hasattr(svc, "_get_fallback_templates")
