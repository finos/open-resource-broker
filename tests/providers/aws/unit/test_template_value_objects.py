"""Unit tests for AWS-specific template value objects.

Covers resource ID validation (subnet/security-group/AMI/fleet/launch
template), AWS instance types, AWS tags, AWS ARNs, the dynamically
extensible ProviderApi/AWSFleetType enums, and AWSConfiguration defaults.

These are pure domain value objects validated against the default
``AWSNamingConfig`` patterns (no AWS API calls, no moto needed).
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import ValidationError

from orb.domain.base.value_objects import PriceType
from orb.providers.aws.domain.template.value_objects import (
    AWSARN,
    AWSConfiguration,
    AWSFleetId,
    AWSFleetType,
    AWSImageId,
    AWSInstanceType,
    AWSLaunchTemplateId,
    AWSSecurityGroupId,
    AWSSubnetId,
    AWSTags,
    ProviderApi,
    ResourceId,
)

# ---------------------------------------------------------------------------
# ResourceId subclasses — AWSSubnetId, AWSSecurityGroupId, AWSFleetId,
# AWSLaunchTemplateId
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSSubnetId:
    def test_valid_subnet_id_accepted(self) -> None:
        subnet = AWSSubnetId(value="subnet-0123456789abcdef0")
        assert subnet.value == "subnet-0123456789abcdef0"

    def test_invalid_subnet_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid AWS Subnet ID format"):
            AWSSubnetId(value="not-a-subnet")

    def test_serializes_to_plain_string(self) -> None:
        subnet = AWSSubnetId(value="subnet-0123456789abcdef0")
        assert subnet.model_dump() == "subnet-0123456789abcdef0"


@pytest.mark.unit
class TestAWSSecurityGroupId:
    def test_valid_security_group_id_accepted(self) -> None:
        sg = AWSSecurityGroupId(value="sg-0123456789abcdef0")
        assert sg.value == "sg-0123456789abcdef0"

    def test_invalid_security_group_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid AWS Security Group ID format"):
            AWSSecurityGroupId(value="sg_bad_format")


@pytest.mark.unit
class TestAWSFleetId:
    def test_valid_fleet_id_accepted(self) -> None:
        fleet = AWSFleetId(value="fleet-0123456789abcdef0")
        assert fleet.value == "fleet-0123456789abcdef0"

    def test_invalid_fleet_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid AWS Fleet ID format"):
            AWSFleetId(value="bad-fleet-id")


@pytest.mark.unit
class TestAWSLaunchTemplateId:
    def test_valid_launch_template_id_accepted(self) -> None:
        lt = AWSLaunchTemplateId(value="lt-0123456789abcdef0")
        assert lt.value == "lt-0123456789abcdef0"

    def test_invalid_launch_template_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid AWS Launch Template ID format"):
            AWSLaunchTemplateId(value="launch-template-xyz")


@pytest.mark.unit
class TestAWSImageId:
    def test_valid_ami_id_accepted(self) -> None:
        ami = AWSImageId(value="ami-0123456789abcdef0")
        assert ami.value == "ami-0123456789abcdef0"

    def test_ssm_path_accepted(self) -> None:
        """AMI pattern also accepts SSM parameter paths."""
        ami = AWSImageId(value="/aws/service/ami-amazon-linux-latest/al2023")
        assert ami.value == "/aws/service/ami-amazon-linux-latest/al2023"

    def test_invalid_ami_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid AWS AMI ID format"):
            AWSImageId(value="not-an-ami")

    def test_to_aws_format_returns_value(self) -> None:
        ami = AWSImageId(value="ami-0123456789abcdef0")
        assert ami.to_aws_format() == "ami-0123456789abcdef0"


@pytest.mark.unit
class TestResourceIdUnknownPatternKey:
    """A ResourceId subclass with a pattern_key absent from AWSNamingConfig
    must raise a clear configuration error rather than an obscure KeyError.
    """

    def test_missing_pattern_in_config_raises_value_error(self) -> None:
        class _UnregisteredResourceId(ResourceId):
            resource_type: ClassVar[str] = "Widget"
            pattern_key: ClassVar[str] = "widget_id_pattern_that_does_not_exist"

        with pytest.raises(ValidationError, match="not found in AWS configuration"):
            _UnregisteredResourceId(value="widget-123")


# ---------------------------------------------------------------------------
# AWSInstanceType
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSInstanceType:
    def test_valid_instance_type_accepted(self) -> None:
        itype = AWSInstanceType(value="m5.large")
        assert itype.value == "m5.large"

    def test_invalid_instance_type_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid AWS instance type format"):
            AWSInstanceType(value="not_an_instance_type!")

    def test_family_property(self) -> None:
        itype = AWSInstanceType(value="m5.large")
        assert itype.family == "m5"

    def test_size_property(self) -> None:
        itype = AWSInstanceType(value="m5.large")
        assert itype.size == "large"

    def test_family_and_size_for_burstable_type(self) -> None:
        itype = AWSInstanceType(value="t2.micro")
        assert itype.family == "t2"
        assert itype.size == "micro"


# ---------------------------------------------------------------------------
# AWSTags
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSTags:
    def test_valid_tags_accepted(self) -> None:
        tags = AWSTags(tags={"Name": "my-instance", "Env": "prod"})
        assert tags.tags == {"Name": "my-instance", "Env": "prod"}

    def test_non_string_tag_value_rejected(self) -> None:
        # dict[str, str] typing rejects non-string values before the custom
        # AWS-specific validator runs; both layers reject the input.
        with pytest.raises(ValidationError):
            AWSTags(tags={"Count": 5})  # type: ignore[dict-item]

    def test_tag_key_length_limit_enforced(self) -> None:
        long_key = "k" * 129
        with pytest.raises(ValidationError, match="AWS tag key length exceeds limit"):
            AWSTags(tags={long_key: "value"})

    def test_tag_value_length_limit_enforced(self) -> None:
        long_value = "v" * 257
        with pytest.raises(ValidationError, match="AWS tag value length exceeds limit"):
            AWSTags(tags={"Name": long_value})

    def test_invalid_tag_key_format_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid AWS tag key format"):
            AWSTags(tags={"bad!key#": "value"})

    def test_to_aws_format_converts_to_key_value_list(self) -> None:
        tags = AWSTags(tags={"Name": "my-instance"})
        assert tags.to_aws_format() == [{"Key": "Name", "Value": "my-instance"}]

    def test_empty_tags_to_aws_format(self) -> None:
        tags = AWSTags(tags={})
        assert tags.to_aws_format() == []


# ---------------------------------------------------------------------------
# AWSARN
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSARN:
    def test_valid_arn_parses_components(self) -> None:
        arn = AWSARN(value="arn:aws:ec2:us-east-1:123456789012:instance/i-0123456789abcdef0")
        assert arn.partition == "aws"
        assert arn.service == "ec2"
        assert arn.region == "us-east-1"
        assert arn.account_id == "123456789012"
        assert arn.resource == "instance/i-0123456789abcdef0"

    def test_invalid_arn_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid AWS ARN format"):
            AWSARN(value="not-an-arn")

    def test_arn_with_colon_in_resource_preserves_full_resource(self) -> None:
        arn = AWSARN(value="arn:aws:iam:us-east-1:123456789012:role:some:nested:resource")
        assert arn.resource == "role:some:nested:resource"

    def test_str_returns_value(self) -> None:
        raw = "arn:aws:ec2:us-east-1:123456789012:instance/i-0123456789abcdef0"
        arn = AWSARN(value=raw)
        assert str(arn) == raw


# ---------------------------------------------------------------------------
# ProviderApi — dynamically extensible string enum
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderApi:
    def test_known_class_attribute_members(self) -> None:
        assert ProviderApi.EC2_FLEET == "EC2Fleet"
        assert ProviderApi.SPOT_FLEET == "SpotFleet"
        assert ProviderApi.ASG == "ASG"
        assert ProviderApi.RUN_INSTANCES == "RunInstances"
        assert ProviderApi.MICRO_VM == "MicroVM"

    def test_raw_string_lookup_resolves_known_value(self) -> None:
        assert ProviderApi("EC2Fleet") == ProviderApi.EC2_FLEET

    def test_unknown_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            ProviderApi("NotARealApi")

    def test_missing_with_non_string_returns_none_and_raises(self) -> None:
        with pytest.raises(ValueError):
            ProviderApi(12345)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AWSFleetType — dynamically extensible string enum
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSFleetType:
    def test_known_class_attribute_members(self) -> None:
        assert AWSFleetType.INSTANT == "instant"
        assert AWSFleetType.REQUEST == "request"
        assert AWSFleetType.MAINTAIN == "maintain"

    def test_raw_string_lookup_resolves_known_value(self) -> None:
        assert AWSFleetType("maintain") == AWSFleetType.MAINTAIN

    def test_unknown_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            AWSFleetType("not-a-fleet-type")

    def test_missing_with_non_string_raises(self) -> None:
        with pytest.raises(ValueError):
            AWSFleetType(object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AWSConfiguration — defaulting behaviour and AWS API serialisation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSConfiguration:
    def test_defaults_applied_when_fields_omitted(self) -> None:
        config = AWSConfiguration(handler_type=ProviderApi.EC2_FLEET)
        assert config.fleet_type == AWSFleetType.REQUEST
        assert config.allocation_strategy == "lowestPrice"
        assert config.price_type == PriceType.ONDEMAND

    def test_explicit_values_are_preserved(self) -> None:
        config = AWSConfiguration(
            handler_type=ProviderApi.SPOT_FLEET,
            fleet_type=AWSFleetType.MAINTAIN,
            allocation_strategy="capacityOptimized",
            price_type=PriceType.SPOT,
        )
        assert config.fleet_type == AWSFleetType.MAINTAIN
        assert config.allocation_strategy == "capacityOptimized"
        assert config.price_type == PriceType.SPOT

    def test_to_aws_api_format_includes_subnet_and_sg_ids(self) -> None:
        config = AWSConfiguration(
            handler_type=ProviderApi.EC2_FLEET,
            subnet_ids=[AWSSubnetId(value="subnet-0123456789abcdef0")],
            security_group_ids=[AWSSecurityGroupId(value="sg-0123456789abcdef0")],
        )
        api_format = config.to_aws_api_format()
        assert api_format["handler_type"] == "EC2Fleet"
        assert api_format["fleet_type"] == "request"
        assert api_format["allocation_strategy"] == "lowestPrice"
        assert api_format["price_type"] == "ondemand"
        assert api_format["subnet_ids"] == ["subnet-0123456789abcdef0"]
        assert api_format["security_group_ids"] == ["sg-0123456789abcdef0"]

    def test_to_aws_api_format_with_no_subnets_or_security_groups(self) -> None:
        config = AWSConfiguration(handler_type=ProviderApi.RUN_INSTANCES)
        api_format = config.to_aws_api_format()
        assert api_format["subnet_ids"] == []
        assert api_format["security_group_ids"] == []
