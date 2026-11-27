"""Template rules of the AWS validation adapter: row shapes, image forms and field rules."""

from unittest.mock import Mock

import pytest

from orb.domain.base.ports.logging_port import LoggingPort
from orb.providers.aws.configuration.validator import AWSProviderConfig
from orb.providers.aws.infrastructure.adapters.aws_validation_adapter import (
    AWSValidationAdapter,
)


@pytest.fixture
def adapter() -> AWSValidationAdapter:
    return AWSValidationAdapter(Mock(spec=AWSProviderConfig), Mock(spec=LoggingPort))


def _row(**extra):
    return {"provider_api": "RunInstances", "image_id": "ami-0abc", **extra}


def _errors(adapter, row):
    return adapter.validate_template_configuration(row)["errors"]


def _warnings(adapter, row):
    return adapter.validate_template_configuration(row)["warnings"]


# Image forms.


@pytest.mark.parametrize(
    "image",
    [
        "ami-0abc",
        "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
        "ssm:/my/param",
        "resolve:ssm:/my/param",
    ],
)
def test_valid_image_forms(adapter, image) -> None:
    assert _errors(adapter, _row(image_id=image)) == []


@pytest.mark.parametrize("image", ["ami", "invalid-ami-id", "ssm", "resolve:ssm"])
def test_malformed_image_is_an_error(adapter, image) -> None:
    assert any("Invalid AWS AMI ID format" in e for e in _errors(adapter, _row(image_id=image)))


@pytest.mark.parametrize("image", [None, ""])
def test_missing_image_is_an_error(adapter, image) -> None:
    row = {"provider_api": "RunInstances", "image_id": image}
    assert "image_id is required for AWS templates" in _errors(adapter, row)


def test_microvm_image_may_be_an_arn(adapter) -> None:
    arn = "arn:aws:lambda:us-east-1:123456789012:microvm-image:my-worker"
    assert _errors(adapter, {"provider_api": "MicroVM", "machine_image": arn}) == []
    assert _errors(adapter, _row(image_id=arn))  # not an AMI reference on other APIs
    other = "arn:other:thing"
    assert _errors(adapter, {"provider_api": "MicroVM", "machine_image": other})


@pytest.mark.parametrize("key", ["machine_image", "image_id", "imageId"])
def test_image_is_read_under_each_key(adapter, key) -> None:
    assert _errors(adapter, {"provider_api": "RunInstances", key: "ami-0abc"}) == []
    errors = _errors(adapter, {"provider_api": "RunInstances", key: "bad"})
    assert any("Invalid AWS AMI ID format: bad" in e for e in errors)


def test_malformed_image_under_either_spelling_is_reported(adapter) -> None:
    # A default and the row's own value can both be present; neither hides the other.
    errors = _errors(adapter, _row(imageId="bad"))
    assert any("Invalid AWS AMI ID format: bad" in e for e in errors)


# Subnets and security groups.


def test_comma_joined_subnets_are_split(adapter) -> None:
    assert _errors(adapter, _row(subnet_ids="subnet-a,subnet-b")) == []
    assert _errors(adapter, _row(subnet_ids="subnet-a, subnet-b,")) == []


def test_bad_comma_joined_subnet_reports_one_error(adapter) -> None:
    errors = _errors(adapter, _row(subnet_ids="subnet-a,bad"))
    assert errors == ["Invalid AWS subnet ID format: bad"]


@pytest.mark.parametrize("key", ["subnet_ids", "subnetIds", "subnet_id", "subnetId"])
@pytest.mark.parametrize("value", [["subnet-a"], ("subnet-a",), "subnet-a"])
def test_subnets_accept_each_key_and_container(adapter, key, value) -> None:
    assert _errors(adapter, _row(**{key: value})) == []
    bad = "bad" if isinstance(value, str) else type(value)(["bad"])
    assert _errors(adapter, _row(**{key: bad})) == ["Invalid AWS subnet ID format: bad"]


@pytest.mark.parametrize(
    "key", ["security_group_ids", "securityGroupIds", "security_group_id", "securityGroupId"]
)
def test_security_groups_accept_each_key(adapter, key) -> None:
    assert _errors(adapter, _row(**{key: "sg-1,sg-2"})) == []
    assert _errors(adapter, _row(**{key: ["bad"]})) == ["Invalid AWS security group ID format: bad"]


def test_own_subnet_is_checked_beside_a_default(adapter) -> None:
    errors = _errors(adapter, _row(subnet_ids=["subnet-default"], subnetIds=["bad"]))
    assert errors == ["Invalid AWS subnet ID format: bad"]


# Provider API and fleet type.


def test_unsupported_api(adapter) -> None:
    assert "Unsupported AWS provider API: VMSS" in _errors(adapter, _row(providerApi="VMSS"))


def test_api_is_read_under_camel_case(adapter) -> None:
    row = {"providerApi": "Nope", "imageId": "ami-1"}
    assert "Unsupported AWS provider API: Nope" in _errors(adapter, row)


@pytest.mark.parametrize(
    "api, fleet_type",
    [("EC2Fleet", "instant"), ("EC2Fleet", "maintain"), ("SpotFleet", "request")],
)
@pytest.mark.parametrize("key", ["fleet_type", "fleetType"])
def test_compatible_fleet_type(adapter, api, fleet_type, key) -> None:
    assert _errors(adapter, _row(provider_api=api, **{key: fleet_type})) == []


@pytest.mark.parametrize("key", ["fleet_type", "fleetType"])
def test_incompatible_fleet_type(adapter, key) -> None:
    errors = _errors(adapter, _row(provider_api="SpotFleet", **{key: "instant"}))
    assert errors == ["Fleet type 'instant' is not compatible with AWS API 'SpotFleet'"]


def test_fleet_type_compare_is_exact(adapter) -> None:
    assert _errors(adapter, _row(provider_api="EC2Fleet", fleet_type="Instant"))


@pytest.mark.parametrize("api", ["ASG", "RunInstances", "MicroVM"])
def test_fleet_type_on_an_api_that_ignores_it_is_a_warning(adapter, api) -> None:
    row = _row(provider_api=api, fleetType="request")
    assert _errors(adapter, row) == []
    assert _warnings(adapter, row) == [f"Fleet type 'request' is ignored by AWS API '{api}'"]


# Percent on demand.


@pytest.mark.parametrize("value", [0, 100, 30, 30.5, "30", "0"])
@pytest.mark.parametrize("key", ["percent_on_demand", "percentOnDemand"])
def test_percent_on_demand_in_range(adapter, key, value) -> None:
    assert _errors(adapter, _row(**{key: value})) == []


@pytest.mark.parametrize("value", [-1, 101, "150", "abc", True, float("nan")])
def test_percent_on_demand_out_of_range_or_not_a_number(adapter, value) -> None:
    errors = _errors(adapter, _row(percentOnDemand=value))
    assert errors == ["percent_on_demand must be between 0 and 100"]


# Machine types and results.


def test_uncommon_machine_type_is_a_warning(adapter) -> None:
    row = _row(vmTypes={"t3.medium": 1, "weird.large": 1})
    assert _errors(adapter, row) == []
    assert _warnings(adapter, row) == ["Uncommon AWS instance type: weird.large"]


def test_result_lists_have_no_duplicates(adapter) -> None:
    errors = _errors(adapter, _row(subnet_ids=["bad"], subnetIds=["bad"]))
    assert errors == ["Invalid AWS subnet ID format: bad"]


def test_enum_fleet_type_is_compared_by_value(adapter) -> None:
    from orb.providers.aws.domain.template.value_objects import AWSFleetType

    row = _row(provider_api="EC2Fleet", fleet_type=AWSFleetType.INSTANT)
    assert _errors(adapter, row) == []
