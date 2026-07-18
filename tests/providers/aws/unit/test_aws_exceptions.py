"""Unit tests for AWS exception classes.

Covers constructors, to_dict(), attribute storage, and inheritance chain
for the full aws_exceptions module.
"""

import pytest

from orb.providers.aws.exceptions.aws_exceptions import (
    AMIValidationError,
    AuthorizationError,
    AWSConfigurationError,
    AWSEntityNotFoundError,
    AWSError,
    AWSInfrastructureError,
    AWSValidationError,
    CostExceededError,
    EC2InstanceNotFoundError,
    FleetRequestError,
    IAMError,
    LaunchError,
    LaunchTemplateError,
    NetworkError,
    QuotaExceededError,
    RateLimitError,
    ResourceCleanupError,
    ResourceInUseError,
    ResourceStateError,
    SecurityGroupValidationError,
    ServiceQuotaError,
    SubnetValidationError,
    TaggingError,
    TerminationError,
)

# ---------------------------------------------------------------------------
# AWSError base class
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSError:
    def test_basic_message(self):
        err = AWSError("something went wrong")
        assert "something went wrong" in str(err)

    def test_to_dict_includes_message(self):
        err = AWSError("test error")
        d = err.to_dict()
        assert d.get("message") == "test error"

    def test_to_dict_includes_aws_error_code(self):
        err = AWSError("bad", aws_error_code="ThrottlingException")
        d = err.to_dict()
        assert d["aws_error_code"] == "ThrottlingException"

    def test_to_dict_includes_aws_error_message(self):
        err = AWSError("bad", aws_error_message="Rate exceeded")
        d = err.to_dict()
        assert d["aws_error_message"] == "Rate exceeded"

    def test_to_dict_includes_aws_request_id(self):
        err = AWSError("bad", aws_request_id="req-abc-123")
        d = err.to_dict()
        assert d["aws_request_id"] == "req-abc-123"

    def test_to_dict_includes_error_source(self):
        err = AWSError("bad", error_source="aws.ec2.RunInstances")
        d = err.to_dict()
        assert d["error_source"] == "aws.ec2.RunInstances"

    def test_to_dict_excludes_none_optional_fields(self):
        err = AWSError("simple error")
        d = err.to_dict()
        assert "aws_error_code" not in d
        assert "aws_error_message" not in d
        assert "aws_request_id" not in d
        assert "error_source" not in d

    def test_error_code_defaults_to_class_name(self):
        err = AWSError("msg")
        assert err.error_code == "AWSError"

    def test_custom_error_code_stored(self):
        err = AWSError("msg", error_code="MY_CODE")
        assert err.error_code == "MY_CODE"
        d = err.to_dict()
        assert d["error_code"] == "MY_CODE"


# ---------------------------------------------------------------------------
# Subclass instantiation (smoke tests)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSErrorSubclasses:
    def test_aws_validation_error(self):
        err = AWSValidationError("validation failed")
        assert isinstance(err, AWSError)
        assert "validation failed" in str(err)

    def test_aws_entity_not_found_error(self):
        err = AWSEntityNotFoundError("not found")
        assert isinstance(err, AWSError)

    def test_authorization_error(self):
        err = AuthorizationError("no permissions")
        assert isinstance(err, AWSError)

    def test_rate_limit_error(self):
        err = RateLimitError("throttled")
        assert isinstance(err, AWSError)

    def test_network_error(self):
        err = NetworkError("timeout")
        assert isinstance(err, AWSError)

    def test_aws_infrastructure_error(self):
        err = AWSInfrastructureError("infra broken")
        assert isinstance(err, AWSError)

    def test_aws_configuration_error(self):
        err = AWSConfigurationError("bad config")
        assert isinstance(err, AWSError)

    def test_resource_in_use_error(self):
        err = ResourceInUseError("resource busy")
        assert isinstance(err, AWSError)

    def test_quota_exceeded_error(self):
        err = QuotaExceededError("quota hit")
        assert isinstance(err, AWSError)


# ---------------------------------------------------------------------------
# ResourceStateError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResourceStateError:
    def test_stores_resource_id(self):
        err = ResourceStateError(
            "bad state", resource_id="i-001", current_state="stopped", expected_states=["running"]
        )
        assert err.resource_id == "i-001"

    def test_stores_current_state(self):
        err = ResourceStateError(
            "bad state", resource_id="i-001", current_state="stopped", expected_states=["running"]
        )
        assert err.current_state == "stopped"

    def test_stores_expected_states(self):
        err = ResourceStateError(
            "bad state",
            resource_id="i-001",
            current_state="stopped",
            expected_states=["running", "pending"],
        )
        assert "running" in err.expected_states

    def test_details_in_dict(self):
        err = ResourceStateError(
            "bad state", resource_id="i-001", current_state="stopped", expected_states=["running"]
        )
        d = err.to_dict()
        assert d["details"]["resource_id"] == "i-001"
        assert d["details"]["current_state"] == "stopped"


# ---------------------------------------------------------------------------
# TaggingError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaggingError:
    def test_stores_resource_id_and_tags(self):
        tags = {"Env": "prod", "Team": "infra"}
        err = TaggingError("tag failed", resource_id="i-001", tags=tags)
        assert err.resource_id == "i-001"
        assert err.tags == tags

    def test_details_in_dict(self):
        err = TaggingError("fail", resource_id="i-001", tags={"k": "v"})
        d = err.to_dict()
        assert d["details"]["resource_id"] == "i-001"
        assert d["details"]["tags"] == {"k": "v"}


# ---------------------------------------------------------------------------
# LaunchError / LaunchTemplateError / FleetRequestError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLaunchErrors:
    def test_launch_error_stores_template_id(self):
        err = LaunchError("launch fail", template_id="tpl-001", launch_params={"k": "v"})
        assert err.template_id == "tpl-001"

    def test_launch_template_error_stores_operation(self):
        err = LaunchTemplateError("lt fail", template_id="lt-001", operation="create")
        assert err.operation == "create"
        assert isinstance(err, LaunchError)

    def test_fleet_request_error_stores_fleet_type(self):
        err = FleetRequestError("fleet fail", fleet_type="EC2Fleet", request_id="req-001")
        assert err.fleet_type == "EC2Fleet"
        assert err.request_id == "req-001"

    def test_fleet_request_error_optional_request_id(self):
        err = FleetRequestError("fleet fail", fleet_type="SpotFleet")
        assert err.request_id is None


# ---------------------------------------------------------------------------
# TerminationError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTerminationError:
    def test_stores_resource_ids(self):
        err = TerminationError("term fail", resource_ids=["i-001", "i-002"])
        assert err.resource_ids == ["i-001", "i-002"]

    def test_details_in_dict(self):
        err = TerminationError("term fail", resource_ids=["i-001"])
        d = err.to_dict()
        assert "i-001" in d["details"]["resource_ids"]


# ---------------------------------------------------------------------------
# EC2InstanceNotFoundError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEc2InstanceNotFoundError:
    def test_stores_instance_id(self):
        err = EC2InstanceNotFoundError("i-dead")
        assert err.instance_id == "i-dead"

    def test_message_includes_instance_id(self):
        err = EC2InstanceNotFoundError("i-dead")
        assert "i-dead" in str(err)

    def test_is_entity_not_found_error(self):
        err = EC2InstanceNotFoundError("i-001")
        assert isinstance(err, AWSEntityNotFoundError)


# ---------------------------------------------------------------------------
# ResourceCleanupError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResourceCleanupError:
    def test_stores_resource_id_and_type(self):
        err = ResourceCleanupError(
            "cleanup fail", resource_id="fleet-001", resource_type="EC2Fleet"
        )
        assert err.resource_id == "fleet-001"
        assert err.resource_type == "EC2Fleet"

    def test_details_in_dict(self):
        err = ResourceCleanupError("cleanup fail", resource_id="r-001", resource_type="ASG")
        d = err.to_dict()
        assert d["details"]["resource_id"] == "r-001"
        assert d["details"]["resource_type"] == "ASG"


# ---------------------------------------------------------------------------
# AMIValidationError / SubnetValidationError / SecurityGroupValidationError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidationSubclasses:
    def test_ami_validation_error_stores_ami_id(self):
        err = AMIValidationError("bad ami", ami_id="ami-0123")
        assert err.ami_id == "ami-0123"
        assert isinstance(err, AWSValidationError)

    def test_subnet_validation_error_stores_subnet_id(self):
        err = SubnetValidationError("bad subnet", subnet_id="subnet-001")
        assert err.subnet_id == "subnet-001"
        assert isinstance(err, AWSValidationError)

    def test_security_group_validation_error_stores_sg_id(self):
        err = SecurityGroupValidationError("bad sg", security_group_id="sg-001")
        assert err.security_group_id == "sg-001"
        assert isinstance(err, AWSValidationError)


# ---------------------------------------------------------------------------
# ServiceQuotaError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestServiceQuotaError:
    def test_stores_quota_fields(self):
        err = ServiceQuotaError(
            "quota exceeded",
            service="ec2",
            quota_name="running-instances",
            current_value=95,
            quota_value=100,
        )
        assert err.service == "ec2"
        assert err.quota_name == "running-instances"
        assert err.current_value == 95
        assert err.quota_value == 100

    def test_is_quota_exceeded_error(self):
        err = ServiceQuotaError(
            "quota", service="ec2", quota_name="x", current_value=1, quota_value=2
        )
        assert isinstance(err, QuotaExceededError)


# ---------------------------------------------------------------------------
# CostExceededError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCostExceededError:
    def test_stores_cost_fields(self):
        err = CostExceededError(
            "cost exceeded", threshold=100.0, current_cost=95.0, projected_cost=120.0
        )
        assert err.threshold == 100.0
        assert err.current_cost == 95.0
        assert err.projected_cost == 120.0

    def test_details_in_dict(self):
        err = CostExceededError(
            "cost exceeded", threshold=50.0, current_cost=40.0, projected_cost=60.0
        )
        d = err.to_dict()
        assert d["details"]["threshold"] == 50.0


# ---------------------------------------------------------------------------
# IAMError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIAMError:
    def test_stores_role_arn_and_permission(self):
        err = IAMError(
            "iam fail", role_arn="arn:aws:iam::123:role/r", permission="ec2:RunInstances"
        )
        assert err.role_arn == "arn:aws:iam::123:role/r"
        assert err.permission == "ec2:RunInstances"

    def test_optional_fields_can_be_none(self):
        err = IAMError("iam fail")
        assert err.role_arn is None
        assert err.permission is None
