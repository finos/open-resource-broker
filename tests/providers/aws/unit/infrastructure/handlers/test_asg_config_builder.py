"""Unit tests for ASGConfigBuilder.

Covers native-spec dispatch (build()), ABIS InstanceRequirements injection,
launch-template patching, the legacy (non-native-spec) config construction
path, template-context preparation, and on-demand/spot capacity splitting.
"""

from typing import Any, cast
from unittest.mock import MagicMock

from orb.domain.request.aggregate import Request
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.asg.config_builder import ASGConfigBuilder


def _make_builder(native_spec_service=None, config_port=None) -> ASGConfigBuilder:
    return ASGConfigBuilder(
        native_spec_service=native_spec_service,
        config_port=config_port,
        logger=MagicMock(),
    )


def _make_template(**overrides: Any) -> AWSTemplate:
    defaults: dict[str, Any] = dict(
        template_id="tpl-1",
        name="test-template",
        provider_api="ASG",
        machine_type="t3.micro",
        machine_types={"t3.micro": 1},
        machine_image="ami-12345678",
        max_machines=5,
        price_type="ondemand",
        subnet_ids=["subnet-1"],
        security_group_ids=["sg-1"],
        tags={},
    )
    defaults.update(overrides)
    return AWSTemplate(**defaults)


def _make_request(request_id: str = "req-1", requested_count: int = 2) -> Request:
    request = MagicMock()
    request.request_id = request_id
    request.requested_count = requested_count
    return cast(Request, request)


# ---------------------------------------------------------------------------
# _api_key()
# ---------------------------------------------------------------------------


def test_api_key_is_asg():
    builder = _make_builder()
    assert builder._api_key() == "asg"


# ---------------------------------------------------------------------------
# _inject_launch_template()
# ---------------------------------------------------------------------------


def test_inject_launch_template_into_mixed_instances_policy():
    builder = _make_builder()
    native_spec: dict[str, Any] = {"MixedInstancesPolicy": {}, "LaunchTemplate": {"stale": True}}
    builder._inject_launch_template(native_spec, _make_template(), "lt-1", "2")

    lt_spec = native_spec["MixedInstancesPolicy"]["LaunchTemplate"]["LaunchTemplateSpecification"]
    assert lt_spec == {"LaunchTemplateId": "lt-1", "Version": "2"}
    assert "LaunchTemplate" not in native_spec


def test_inject_launch_template_does_not_override_existing_lt_spec():
    builder = _make_builder()
    native_spec: dict[str, Any] = {
        "MixedInstancesPolicy": {
            "LaunchTemplate": {
                "LaunchTemplateSpecification": {"LaunchTemplateId": "lt-existing", "Version": "1"}
            }
        }
    }
    builder._inject_launch_template(native_spec, _make_template(), "lt-new", "9")

    lt_spec = native_spec["MixedInstancesPolicy"]["LaunchTemplate"]["LaunchTemplateSpecification"]
    assert lt_spec == {"LaunchTemplateId": "lt-existing", "Version": "1"}


def test_inject_launch_template_plain_spec_without_mixed_instances_policy():
    builder = _make_builder()
    native_spec: dict[str, Any] = {}
    builder._inject_launch_template(native_spec, _make_template(), "lt-1", "3")

    assert native_spec["LaunchTemplate"] == {"LaunchTemplateId": "lt-1", "Version": "3"}


# ---------------------------------------------------------------------------
# build() — dispatch between native-spec and legacy paths
# ---------------------------------------------------------------------------


def test_build_uses_legacy_path_without_native_spec_service():
    builder = _make_builder(native_spec_service=None)
    template = _make_template()
    request = _make_request()

    result = builder.build("my-asg", template, request, "lt-1", "1")

    assert result["AutoScalingGroupName"] == "my-asg"
    assert result["DesiredCapacity"] == request.requested_count


def test_build_returns_native_spec_when_processing_succeeds():
    builder = _make_builder(native_spec_service=MagicMock())
    template = _make_template()
    request = _make_request()
    builder._process_native_spec = MagicMock(return_value={"Foo": "bar"})  # type: ignore[method-assign]
    builder._ensure_abis_in_native_spec = MagicMock()  # type: ignore[method-assign]

    result = builder.build("my-asg", template, request, "lt-1", "1")

    assert result["AutoScalingGroupName"] == "my-asg"
    assert result["NewInstancesProtectedFromScaleIn"] is True
    assert result["Foo"] == "bar"


def test_build_falls_back_to_render_default_when_native_spec_empty():
    builder = _make_builder(native_spec_service=MagicMock())
    template = _make_template()
    request = _make_request()
    builder._process_native_spec = MagicMock(return_value=None)  # type: ignore[method-assign]
    builder._render_default = MagicMock(return_value={"rendered": "default"})  # type: ignore[method-assign]

    result = builder.build("my-asg", template, request, "lt-1", "1")

    assert result == {"rendered": "default"}
    builder._render_default.assert_called_once()


# ---------------------------------------------------------------------------
# _ensure_abis_in_native_spec()
# ---------------------------------------------------------------------------


def test_ensure_abis_noop_without_instance_requirements(monkeypatch):
    builder = _make_builder()
    monkeypatch.setattr(AWSTemplate, "get_instance_requirements_payload", lambda self: None)
    native_spec: dict[str, Any] = {}
    builder._ensure_abis_in_native_spec(native_spec, _make_template(), "lt-1", "1")
    assert native_spec == {}


def test_ensure_abis_injects_instance_requirements_override(monkeypatch):
    builder = _make_builder()
    monkeypatch.setattr(
        AWSTemplate,
        "get_instance_requirements_payload",
        lambda self: {"VCpuCount": {"Min": 2}},
    )
    native_spec: dict[str, Any] = {"LaunchTemplate": {"stale": True}}
    builder._ensure_abis_in_native_spec(native_spec, _make_template(), "lt-1", "2")

    mip = native_spec["MixedInstancesPolicy"]
    overrides = mip["LaunchTemplate"]["Overrides"]
    assert overrides == [{"InstanceRequirements": {"VCpuCount": {"Min": 2}}}]
    assert mip["LaunchTemplate"]["LaunchTemplateSpecification"] == {
        "LaunchTemplateId": "lt-1",
        "Version": "2",
    }
    assert "LaunchTemplate" not in native_spec


def test_ensure_abis_respects_existing_instance_requirements_override(monkeypatch):
    builder = _make_builder()
    monkeypatch.setattr(
        AWSTemplate,
        "get_instance_requirements_payload",
        lambda self: {"VCpuCount": {"Min": 2}},
    )
    existing_override = {"InstanceRequirements": {"VCpuCount": {"Min": 99}}}
    native_spec: dict[str, Any] = {
        "MixedInstancesPolicy": {"LaunchTemplate": {"Overrides": [existing_override]}}
    }
    builder._ensure_abis_in_native_spec(native_spec, _make_template(), "lt-1", "2")

    overrides = native_spec["MixedInstancesPolicy"]["LaunchTemplate"]["Overrides"]
    assert overrides == [existing_override]  # untouched


# ---------------------------------------------------------------------------
# _build_legacy()
# ---------------------------------------------------------------------------


def test_build_legacy_basic_fields():
    builder = _make_builder()
    template = _make_template(machine_types={}, subnet_ids=[])
    request = _make_request(requested_count=3)

    result = builder._build_legacy("my-asg", template, request, "lt-1", "4")

    assert result["AutoScalingGroupName"] == "my-asg"
    assert result["MinSize"] == 0
    assert result["MaxSize"] == 6
    assert result["DesiredCapacity"] == 3
    assert result["LaunchTemplate"] == {"LaunchTemplateId": "lt-1", "Version": "4"}
    assert "VPCZoneIdentifier" not in result


def test_build_legacy_with_abis_instance_requirements(monkeypatch):
    builder = _make_builder()
    monkeypatch.setattr(
        AWSTemplate,
        "get_instance_requirements_payload",
        lambda self: {"VCpuCount": {"Min": 2}},
    )
    template = _make_template()
    request = _make_request()

    result = builder._build_legacy("my-asg", template, request, "lt-1", "1")

    mip = result["MixedInstancesPolicy"]
    assert mip["LaunchTemplate"]["Overrides"] == [
        {"InstanceRequirements": {"VCpuCount": {"Min": 2}}}
    ]
    assert "LaunchTemplate" not in result


def test_build_legacy_with_weighted_machine_types():
    builder = _make_builder()
    template = _make_template(machine_types={"t3.micro": 2, "t3.small": 0})
    request = _make_request()

    result = builder._build_legacy("my-asg", template, request, "lt-1", "1")

    overrides = result["MixedInstancesPolicy"]["LaunchTemplate"]["Overrides"]
    assert {"InstanceType": "t3.micro", "WeightedCapacity": "2"} in overrides
    assert {"InstanceType": "t3.small"} in overrides  # falsy weight omitted


def test_build_legacy_spot_distribution_with_percent_on_demand():
    builder = _make_builder()
    template = _make_template(machine_types={}, price_type="ondemand", percent_on_demand=30)
    request = _make_request()

    result = builder._build_legacy("my-asg", template, request, "lt-1", "1")

    distribution = result["MixedInstancesPolicy"]["InstancesDistribution"]
    assert distribution["OnDemandBaseCapacity"] == 0
    assert distribution["OnDemandPercentageAboveBaseCapacity"] == 30
    assert "LaunchTemplate" not in result  # replaced by MixedInstancesPolicy


def test_build_legacy_spot_distribution_with_allocation_strategy():
    builder = _make_builder()
    template = _make_template(
        machine_types={}, price_type="spot", allocation_strategy="lowest-price"
    )
    request = _make_request()

    result = builder._build_legacy("my-asg", template, request, "lt-1", "1")

    distribution = result["MixedInstancesPolicy"]["InstancesDistribution"]
    assert distribution["SpotAllocationStrategy"] == template.get_asg_allocation_strategy()
    assert distribution["OnDemandPercentageAboveBaseCapacity"] == 0


def test_build_legacy_spot_distribution_reuses_existing_mixed_instances_policy():
    builder = _make_builder()
    template = _make_template(
        machine_types={"t3.micro": 1}, price_type="spot", allocation_strategy=None
    )
    request = _make_request()

    result = builder._build_legacy("my-asg", template, request, "lt-1", "1")

    mip = result["MixedInstancesPolicy"]
    assert mip["LaunchTemplate"]["Overrides"]  # populated by the machine_types branch
    assert "InstancesDistribution" in mip


def test_build_legacy_includes_subnet_ids_and_context():
    builder = _make_builder()
    template = _make_template(subnet_ids=["subnet-1", "subnet-2"], context="user-data-script")
    request = _make_request()

    result = builder._build_legacy("my-asg", template, request, "lt-1", "1")

    assert result["VPCZoneIdentifier"] == "subnet-1,subnet-2"
    assert result["Context"] == "user-data-script"


# ---------------------------------------------------------------------------
# _prepare_template_context()
# ---------------------------------------------------------------------------


def test_prepare_template_context_requires_config_port():
    from orb.providers.aws.exceptions.aws_exceptions import AWSConfigurationError

    builder = _make_builder(config_port=None)
    template = _make_template()
    request = _make_request()

    try:
        builder._prepare_template_context(template, request)
        raise AssertionError("expected AWSConfigurationError")
    except AWSConfigurationError:
        pass


def test_prepare_template_context_builds_full_context():
    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = "orb-"
    builder = _make_builder(config_port=config_port)
    template = _make_template(machine_types={"t3.micro": 2}, subnet_ids=["subnet-1"])
    request = _make_request(request_id="req-123", requested_count=4)

    context = builder._prepare_template_context(template, request)

    assert context["asg_name"] == "orb-req-123"
    assert context["desired_capacity"] == 4
    assert context["max_size"] == 8
    assert context["has_machine_types"] is True
    assert context["has_abis"] is False
    assert context["machine_types_overrides"] == [
        {"instance_type": "t3.micro", "weighted_capacity": "2"}
    ]
    assert context["on_demand_count"] == 4
    assert context["spot_count"] == 0
    assert context["vpc_zone_identifier"] == "subnet-1"
    assert context["has_subnets"] is True


def test_prepare_template_context_abis_suppresses_machine_types_overrides(monkeypatch):
    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = "orb-"
    monkeypatch.setattr(
        AWSTemplate,
        "get_instance_requirements_payload",
        lambda self: {"VCpuCount": {"Min": 2}},
    )
    builder = _make_builder(config_port=config_port)
    template = _make_template(machine_types={"t3.micro": 1})
    request = _make_request()

    context = builder._prepare_template_context(template, request)

    assert context["has_abis"] is True
    assert context["has_machine_types"] is False
    assert context["machine_types_overrides"] == []


# ---------------------------------------------------------------------------
# _calculate_capacity_distribution()
# ---------------------------------------------------------------------------


def test_calculate_capacity_distribution_with_percent_on_demand():
    builder = _make_builder()
    template = _make_template(percent_on_demand=25)
    result = builder._calculate_capacity_distribution(template, 8)
    assert result == {"on_demand_count": 2, "spot_count": 6}


def test_calculate_capacity_distribution_all_spot():
    builder = _make_builder()
    template = _make_template(price_type="spot", percent_on_demand=None)
    result = builder._calculate_capacity_distribution(template, 5)
    assert result == {"on_demand_count": 0, "spot_count": 5}


def test_calculate_capacity_distribution_all_on_demand_default():
    builder = _make_builder()
    template = _make_template(price_type="ondemand", percent_on_demand=None)
    result = builder._calculate_capacity_distribution(template, 5)
    assert result == {"on_demand_count": 5, "spot_count": 0}
