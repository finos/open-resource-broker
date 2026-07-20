"""Unit tests for AWSTemplateAdapter.

Covers validate_template, validate_field_values, resolve_ami_id,
_determine_provider_api, get_supported_provider_apis, and error paths.

All AWS/SSM client calls are replaced with MagicMock.
"""

from unittest.mock import MagicMock, patch

import pytest

from orb.providers.aws.exceptions.aws_exceptions import AWSValidationError
from orb.providers.aws.infrastructure.adapters.template_adapter import (
    AWSTemplateAdapter,
    create_aws_template_adapter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SENTINEL = object()


def _make_adapter(config_manager=None):
    """Build AWSTemplateAdapter with all dependencies mocked."""
    template_config_manager = MagicMock()
    aws_client = MagicMock()
    logger = MagicMock()
    return AWSTemplateAdapter(
        template_config_manager=template_config_manager,
        aws_client=aws_client,
        logger=logger,
        config_manager=config_manager,
    )


def _make_template(
    image_id="ami-0abc123def456789a",
    machine_types=_SENTINEL,
    subnet_ids=None,
    security_group_ids=None,
    fleet_type=None,
    spot_price=None,
    provider_api=None,
    abis=None,
):
    tmpl = MagicMock()
    tmpl.machine_image = image_id
    # Use a sentinel so callers can pass machine_types={} explicitly
    tmpl.machine_types = {"t3.medium": 1} if machine_types is _SENTINEL else machine_types
    tmpl.subnet_ids = subnet_ids if subnet_ids is not None else ["subnet-0abc12345def67890"]
    tmpl.security_group_ids = security_group_ids if security_group_ids is not None else []
    tmpl.fleet_type = fleet_type
    tmpl.spot_price = spot_price
    tmpl.provider_api = provider_api
    tmpl.abis_instance_requirements = abis
    return tmpl


# ---------------------------------------------------------------------------
# _is_valid_ami_format
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsValidAmiFormat:
    def test_valid_8_char_ami(self):
        adapter = _make_adapter()
        assert adapter._is_valid_ami_format("ami-0abc1234") is True

    def test_valid_17_char_ami(self):
        adapter = _make_adapter()
        assert adapter._is_valid_ami_format("ami-0abcdef1234567890") is True

    def test_too_short_ami_invalid(self):
        adapter = _make_adapter()
        assert adapter._is_valid_ami_format("ami-0abc") is False

    def test_no_prefix_invalid(self):
        adapter = _make_adapter()
        assert adapter._is_valid_ami_format("0abc123def456789a") is False

    def test_uppercase_invalid(self):
        adapter = _make_adapter()
        assert adapter._is_valid_ami_format("ami-0ABC123DEF456789") is False

    def test_ssm_path_invalid(self):
        adapter = _make_adapter()
        assert adapter._is_valid_ami_format("/aws/service/ami-amazon-linux-latest") is False


# ---------------------------------------------------------------------------
# _is_valid_instance_type
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsValidInstanceType:
    def test_valid_t3_medium(self):
        adapter = _make_adapter()
        assert adapter._is_valid_instance_type("t3.medium") is True

    def test_valid_c5_xlarge(self):
        adapter = _make_adapter()
        assert adapter._is_valid_instance_type("c5.xlarge") is True

    def test_invalid_no_dot(self):
        adapter = _make_adapter()
        assert adapter._is_valid_instance_type("t3medium") is False

    def test_invalid_uppercase(self):
        adapter = _make_adapter()
        assert adapter._is_valid_instance_type("T3.medium") is False


# ---------------------------------------------------------------------------
# _is_valid_subnet_format
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsValidSubnetFormat:
    def test_valid_subnet(self):
        adapter = _make_adapter()
        assert adapter._is_valid_subnet_format("subnet-0abc12345def67890") is True

    def test_invalid_no_prefix(self):
        adapter = _make_adapter()
        assert adapter._is_valid_subnet_format("0abc12345def67890") is False

    def test_invalid_too_short(self):
        adapter = _make_adapter()
        assert adapter._is_valid_subnet_format("subnet-abc") is False


# ---------------------------------------------------------------------------
# _is_valid_security_group_format
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsValidSecurityGroupFormat:
    def test_valid_sg(self):
        adapter = _make_adapter()
        assert adapter._is_valid_security_group_format("sg-0abc12345def67890") is True

    def test_invalid_no_prefix(self):
        adapter = _make_adapter()
        assert adapter._is_valid_security_group_format("0abc12345def67890") is False


# ---------------------------------------------------------------------------
# validate_field_values
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateFieldValues:
    def test_valid_template_returns_no_errors(self):
        adapter = _make_adapter()
        tmpl = _make_template()
        errors = adapter.validate_field_values(tmpl)
        assert errors == {}

    def test_missing_image_id_returns_error(self):
        adapter = _make_adapter()
        tmpl = _make_template(image_id=None)
        errors = adapter.validate_field_values(tmpl)
        assert "image_id" in errors

    def test_invalid_ami_format_returns_error(self):
        adapter = _make_adapter()
        tmpl = _make_template(image_id="bad-format")
        errors = adapter.validate_field_values(tmpl)
        assert "image_id" in errors
        assert "bad-format" in errors["image_id"]

    def test_missing_machine_types_and_abis_returns_error(self):
        adapter = _make_adapter()
        tmpl = _make_template(machine_types={}, abis=None)
        errors = adapter.validate_field_values(tmpl)
        assert "machine_types" in errors

    def test_abis_alone_satisfies_machine_types_requirement(self):
        adapter = _make_adapter()
        tmpl = _make_template(machine_types={}, abis={"VCpuCount": {"Min": 2}})
        errors = adapter.validate_field_values(tmpl)
        assert "machine_types" not in errors

    def test_invalid_instance_type_in_machine_types_returns_error(self):
        adapter = _make_adapter()
        tmpl = _make_template(machine_types={"INVALID_TYPE": 1})
        errors = adapter.validate_field_values(tmpl)
        assert "machine_types" in errors

    def test_missing_subnet_ids_returns_error(self):
        adapter = _make_adapter()
        tmpl = _make_template(subnet_ids=[])
        errors = adapter.validate_field_values(tmpl)
        assert "subnet_ids" in errors

    def test_invalid_subnet_format_returns_error(self):
        adapter = _make_adapter()
        tmpl = _make_template(subnet_ids=["invalid-subnet"])
        errors = adapter.validate_field_values(tmpl)
        assert "subnet_ids" in errors

    def test_invalid_security_group_format_returns_error(self):
        adapter = _make_adapter()
        tmpl = _make_template(security_group_ids=["bad-sg"])
        errors = adapter.validate_field_values(tmpl)
        assert "security_group_ids" in errors

    def test_valid_security_group_no_error(self):
        adapter = _make_adapter()
        tmpl = _make_template(security_group_ids=["sg-0abc12345def67890"])
        errors = adapter.validate_field_values(tmpl)
        assert "security_group_ids" not in errors


# ---------------------------------------------------------------------------
# _validate_aws_configurations (fleet_type / spot_price)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateAwsConfigurations:
    def test_valid_fleet_type_instant(self):
        adapter = _make_adapter()
        tmpl = _make_template(fleet_type="instant")
        errors = adapter._validate_aws_configurations(tmpl)
        assert errors == []

    def test_valid_fleet_type_maintain(self):
        adapter = _make_adapter()
        tmpl = _make_template(fleet_type="maintain")
        errors = adapter._validate_aws_configurations(tmpl)
        assert errors == []

    def test_invalid_fleet_type_returns_error(self):
        adapter = _make_adapter()
        tmpl = _make_template(fleet_type="continuous")
        errors = adapter._validate_aws_configurations(tmpl)
        assert any("Invalid fleet type" in e for e in errors)

    def test_valid_spot_price_no_error(self):
        adapter = _make_adapter()
        tmpl = _make_template(spot_price="0.05")
        errors = adapter._validate_aws_configurations(tmpl)
        assert errors == []

    def test_invalid_spot_price_returns_error(self):
        adapter = _make_adapter()
        tmpl = _make_template(spot_price="not-a-price")
        errors = adapter._validate_aws_configurations(tmpl)
        assert any("Invalid spot price" in e for e in errors)


# ---------------------------------------------------------------------------
# _determine_provider_api
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDetermineProviderApi:
    def test_defaults_to_ec2_fleet(self):
        adapter = _make_adapter()
        tmpl = _make_template()
        assert adapter._determine_provider_api(tmpl) == "EC2Fleet"

    def test_spot_price_returns_spot_fleet(self):
        adapter = _make_adapter()
        tmpl = _make_template(spot_price="0.10")
        assert adapter._determine_provider_api(tmpl) == "SpotFleet"

    def test_fleet_type_request_returns_spot_fleet(self):
        adapter = _make_adapter()
        tmpl = _make_template(fleet_type="request")
        assert adapter._determine_provider_api(tmpl) == "SpotFleet"


# ---------------------------------------------------------------------------
# extend_template_fields
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtendTemplateFields:
    def test_sets_provider_api_when_absent(self):
        adapter = _make_adapter()
        tmpl = _make_template(provider_api=None)
        result = adapter.extend_template_fields(tmpl)
        assert result.provider_api is not None

    def test_does_not_overwrite_existing_provider_api(self):
        adapter = _make_adapter()
        tmpl = _make_template(provider_api="ASG")
        result = adapter.extend_template_fields(tmpl)
        assert result.provider_api == "ASG"


# ---------------------------------------------------------------------------
# resolve_ami_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveAmiId:
    def test_valid_ami_returned_as_is(self):
        adapter = _make_adapter()
        ami = "ami-0abc12345def67890"
        assert adapter.resolve_ami_id(ami) == ami

    def test_ssm_path_resolved_via_ssm_client(self):
        adapter = _make_adapter()
        ssm_path = "/my/ssm/path"
        resolved_ami = "ami-0abcdef1234567890"
        # Clear the class-level cache to avoid cross-test pollution
        AWSTemplateAdapter._ssm_parameter_cache.clear()
        adapter._aws_client.ssm_client.get_parameter.return_value = {
            "Parameter": {"Value": resolved_ami}
        }
        result = adapter.resolve_ami_id(ssm_path)
        assert result == resolved_ami

    def test_ssm_invalid_ami_raises_aws_validation_error(self):
        adapter = _make_adapter()
        AWSTemplateAdapter._ssm_parameter_cache.clear()
        adapter._aws_client.ssm_client.get_parameter.return_value = {
            "Parameter": {"Value": "not-an-ami"}
        }
        with pytest.raises(AWSValidationError):
            adapter.resolve_ami_id("/some/ssm/path")

    def test_ssm_client_error_raises_aws_validation_error(self):
        adapter = _make_adapter()
        AWSTemplateAdapter._ssm_parameter_cache.clear()
        adapter._aws_client.ssm_client.get_parameter.side_effect = RuntimeError("SSM down")
        with pytest.raises(AWSValidationError):
            adapter.resolve_ami_id("/broken/path")

    def test_amazon_linux_alias_resolved_via_ssm(self):
        adapter = _make_adapter()
        AWSTemplateAdapter._ssm_parameter_cache.clear()
        resolved_ami = "ami-0abcdef1234567890"
        adapter._aws_client.ssm_client.get_parameter.return_value = {
            "Parameter": {"Value": resolved_ami}
        }
        result = adapter.resolve_ami_id("amazon-linux-2")
        assert result == resolved_ami

    def test_unknown_alias_returned_as_is_with_warning(self):
        adapter = _make_adapter()
        unknown = "my-custom-alias"
        result = adapter.resolve_ami_id(unknown)
        assert result == unknown
        adapter._logger.warning.assert_called()

    def test_ssm_cache_used_on_second_call(self):
        adapter = _make_adapter()
        # Use a unique path to avoid cross-test interference
        ssm_path = "/cached/path/unique-test-key"
        # Must be a valid AMI ID (8-17 hex chars after ami-)
        cached_ami = "ami-0abc12345def6789"  # exactly 16 hex chars — valid
        AWSTemplateAdapter._ssm_parameter_cache.clear()
        AWSTemplateAdapter._ssm_parameter_cache[ssm_path] = cached_ami
        result = adapter.resolve_ami_id(ssm_path)
        assert result == cached_ami
        # SSM client should NOT have been called
        adapter._aws_client.ssm_client.get_parameter.assert_not_called()


# ---------------------------------------------------------------------------
# validate_ami_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateAmiId:
    def test_returns_true_for_available_ami(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.describe_images.return_value = {
            "Images": [{"State": "available"}]
        }
        assert adapter.validate_ami_id("ami-0abc12345def67890") is True

    def test_returns_false_for_non_available_ami(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.describe_images.return_value = {
            "Images": [{"State": "pending"}]
        }
        assert adapter.validate_ami_id("ami-0abc12345def67890") is False

    def test_returns_false_when_no_images(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.describe_images.return_value = {"Images": []}
        assert adapter.validate_ami_id("ami-0abc12345def67890") is False

    def test_returns_false_on_exception(self):
        adapter = _make_adapter()
        adapter._aws_client.ec2_client.describe_images.side_effect = RuntimeError("API down")
        assert adapter.validate_ami_id("ami-0abc12345def67890") is False


# ---------------------------------------------------------------------------
# get_supported_provider_apis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSupportedProviderApis:
    def test_returns_apis_from_config_manager(self):
        config_manager = MagicMock()
        config_manager.get_raw_config.return_value = {
            "provider": {
                "provider_defaults": {
                    "aws": {"handlers": {"EC2Fleet": {}, "ASG": {}, "SpotFleet": {}}}
                }
            }
        }
        adapter = _make_adapter(config_manager=config_manager)
        apis = adapter.get_supported_provider_apis()
        assert set(apis) == {"EC2Fleet", "ASG", "SpotFleet"}

    def test_returns_empty_on_exception(self):
        config_manager = MagicMock()
        config_manager.get_raw_config.side_effect = RuntimeError("config error")
        adapter = _make_adapter(config_manager=config_manager)
        apis = adapter.get_supported_provider_apis()
        assert apis == []

    def test_falls_back_to_di_container_when_no_config_manager(self):
        adapter = _make_adapter(config_manager=None)
        mock_container = MagicMock()
        mock_config_mgr = MagicMock()
        mock_config_mgr.get_raw_config.return_value = {
            "provider": {
                "provider_defaults": {"aws": {"handlers": {"EC2Fleet": {}, "RunInstances": {}}}}
            }
        }
        mock_container.get.return_value = mock_config_mgr
        with patch("orb.infrastructure.di.container.get_container", return_value=mock_container):
            apis = adapter.get_supported_provider_apis()
        assert "EC2Fleet" in apis


# ---------------------------------------------------------------------------
# get_supported_fields / get_provider_api / get_adapter_info
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMiscMethods:
    def test_get_supported_fields_returns_list(self):
        adapter = _make_adapter()
        fields = adapter.get_supported_fields()
        assert isinstance(fields, list)
        assert "image_id" in fields
        assert "subnet_ids" in fields

    def test_get_provider_api_returns_ec2_fleet(self):
        adapter = _make_adapter()
        assert adapter.get_provider_api() == "EC2Fleet"

    def test_get_adapter_info_has_expected_keys(self):
        adapter = _make_adapter()
        info = adapter.get_adapter_info()
        assert info["adapter_name"] == "AWSTemplateAdapter"
        assert info["provider_type"] == "aws"
        assert "ami_resolution" in info["features"]


# ---------------------------------------------------------------------------
# validate_template (integration of sub-validators)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateTemplate:
    def test_valid_template_returns_empty_list(self):
        adapter = _make_adapter()
        tmpl = _make_template()
        errors = adapter.validate_template(tmpl)
        assert errors == []

    def test_missing_image_id_produces_error(self):
        adapter = _make_adapter()
        tmpl = _make_template(image_id=None)
        errors = adapter.validate_template(tmpl)
        assert len(errors) > 0
        assert any("Image ID" in e or "image_id" in e.lower() for e in errors)

    def test_invalid_fleet_type_produces_error(self):
        adapter = _make_adapter()
        tmpl = _make_template(fleet_type="unknown-type")
        errors = adapter.validate_template(tmpl)
        assert any("fleet type" in e.lower() for e in errors)

    def test_no_empty_string_errors_in_output(self):
        adapter = _make_adapter()
        tmpl = _make_template()
        errors = adapter.validate_template(tmpl)
        assert all(e for e in errors)  # no empty strings


# ---------------------------------------------------------------------------
# resolve_template_references
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveTemplateReferences:
    def test_resolves_ssm_path_ami(self):
        adapter = _make_adapter()
        AWSTemplateAdapter._ssm_parameter_cache.clear()
        resolved_ami = "ami-0abcdef1234567890"
        adapter._aws_client.ssm_client.get_parameter.return_value = {
            "Parameter": {"Value": resolved_ami}
        }
        tmpl = _make_template(image_id="/my/ami/path")
        result = adapter.resolve_template_references(tmpl)
        assert result.machine_image == resolved_ami

    def test_leaves_valid_ami_unchanged(self):
        adapter = _make_adapter()
        ami = "ami-0abc12345def67890"
        tmpl = _make_template(image_id=ami)
        result = adapter.resolve_template_references(tmpl)
        assert result.machine_image == ami

    def test_no_image_id_skips_resolution(self):
        adapter = _make_adapter()
        tmpl = _make_template(image_id=None)
        # should not raise
        result = adapter.resolve_template_references(tmpl)
        assert result is not None


# ---------------------------------------------------------------------------
# create_aws_template_adapter factory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateAwsTemplateAdapterFactory:
    def test_returns_aws_template_adapter_instance(self):
        aws_client = MagicMock()
        logger = MagicMock()
        config = MagicMock()
        with patch(
            "orb.infrastructure.template.configuration_manager.TemplateConfigurationManager"
        ):
            adapter = create_aws_template_adapter(aws_client, logger, config)
        assert isinstance(adapter, AWSTemplateAdapter)
