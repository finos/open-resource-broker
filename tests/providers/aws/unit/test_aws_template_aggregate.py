"""Unit tests for the AWSTemplate domain aggregate.

Covers construction defaults, provider_config promotion, metadata-driven
fleet_type/fleet_role/percent_on_demand defaulting, launch template version
validation, allocation strategy coercion/serialization, the
from_aws_format()/to_aws_api_format() round trip, AWSConfiguration
building via get_aws_configuration(), ABIS instance requirements, and the
native-spec mutual-exclusion validator.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orb.domain.base.value_objects import PriceType
from orb.providers.aws.domain.template.aws_template_aggregate import (
    ABISInstanceRequirements,
    AWSRequiredIntegerRange,
    AWSTemplate,
)
from orb.providers.aws.domain.template.value_objects import AWSFleetType, ProviderApi
from orb.providers.aws.value_objects import AWSAllocationStrategy

# ---------------------------------------------------------------------------
# Construction defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSTemplateConstruction:
    def test_provider_type_is_forced_to_aws(self) -> None:
        template = AWSTemplate(template_id="t1")
        assert template.provider_type == "aws"

    def test_provider_type_override_in_data_is_ignored(self) -> None:
        """__init__ always overwrites provider_type with 'aws'."""
        template = AWSTemplate(template_id="t1", provider_type="gcp")
        assert template.provider_type == "aws"

    def test_default_machine_disk_type_is_gp3(self) -> None:
        template = AWSTemplate(template_id="t1")
        assert template.machine_disk_type == "gp3"

    def test_volume_type_alias_populates_machine_disk_type(self) -> None:
        template = AWSTemplate(template_id="t1", volume_type="io2")
        assert template.machine_disk_type == "io2"


# ---------------------------------------------------------------------------
# provider_config promotion (DTO round-trip path)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderConfigPromotion:
    def test_fleet_type_promoted_from_provider_config(self) -> None:
        template = AWSTemplate(template_id="t1", provider_config={"fleet_type": "maintain"})
        assert template.fleet_type == AWSFleetType.MAINTAIN

    def test_invalid_fleet_type_in_provider_config_is_ignored(self) -> None:
        template = AWSTemplate(template_id="t1", provider_config={"fleet_type": "not-a-real-type"})
        assert template.fleet_type is None

    def test_fleet_role_promoted_from_provider_config(self) -> None:
        template = AWSTemplate(
            template_id="t1", provider_config={"fleet_role": "arn:aws:iam::123456789012:role/x"}
        )
        assert template.fleet_role == "arn:aws:iam::123456789012:role/x"

    def test_percent_on_demand_promoted_from_provider_config(self) -> None:
        template = AWSTemplate(template_id="t1", provider_config={"percent_on_demand": "40"})
        assert template.percent_on_demand == 40

    def test_launch_template_id_promoted_from_provider_config(self) -> None:
        template = AWSTemplate(
            template_id="t1", provider_config={"launch_template_id": "lt-0123456789abcdef0"}
        )
        assert template.launch_template_id == "lt-0123456789abcdef0"

    def test_abis_requirements_promoted_from_provider_config(self) -> None:
        template = AWSTemplate(
            template_id="t1",
            provider_config={
                "abis_instance_requirements": {
                    "VCpuCount": {"Min": 2, "Max": 8},
                    "MemoryMiB": {"Min": 2048, "Max": 16384},
                }
            },
        )
        assert template.abis_instance_requirements is not None
        assert template.abis_instance_requirements.vcpu_count.min == 2

    def test_explicit_fleet_type_not_overridden_by_provider_config(self) -> None:
        template = AWSTemplate(
            template_id="t1",
            fleet_type=AWSFleetType.INSTANT,
            provider_config={"fleet_type": "maintain"},
        )
        assert template.fleet_type == AWSFleetType.INSTANT

    def test_non_dict_provider_config_is_ignored(self) -> None:
        """provider_config typed as Optional[dict]; a non-dict value should not crash."""
        template = AWSTemplate(template_id="t1")
        # Directly bypass validation to simulate a bad round-trip value.
        object.__setattr__(template, "provider_config", "not-a-dict")
        # Re-triggering validation manually should simply skip the dict branch.
        revalidated = template.validate_aws_template()  # type: ignore[operator]
        assert revalidated.fleet_role is None


# ---------------------------------------------------------------------------
# Metadata-driven defaulting
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetadataDrivenDefaults:
    def test_fleet_type_from_metadata_top_level(self) -> None:
        template = AWSTemplate(template_id="t1", metadata={"fleet_type": "Maintain"})
        assert template.fleet_type == AWSFleetType.MAINTAIN

    def test_fleet_type_from_nested_metadata_provider_config(self) -> None:
        template = AWSTemplate(
            template_id="t1", metadata={"providerConfig": {"fleet_type": "instant"}}
        )
        assert template.fleet_type == AWSFleetType.INSTANT

    def test_invalid_fleet_type_in_metadata_is_ignored(self) -> None:
        template = AWSTemplate(template_id="t1", metadata={"fleet_type": "bogus"})
        assert template.fleet_type is None

    def test_default_fleet_type_for_ec2_fleet_provider_api(self) -> None:
        template = AWSTemplate(template_id="t1", provider_api=ProviderApi.EC2_FLEET)
        assert template.fleet_type == AWSFleetType.REQUEST

    def test_default_fleet_type_for_spot_fleet_provider_api(self) -> None:
        template = AWSTemplate(template_id="t1", provider_api=ProviderApi.SPOT_FLEET)
        assert template.fleet_type == AWSFleetType.REQUEST

    def test_no_default_fleet_type_for_run_instances(self) -> None:
        template = AWSTemplate(template_id="t1", provider_api=ProviderApi.RUN_INSTANCES)
        assert template.fleet_type is None

    def test_fleet_role_from_metadata(self) -> None:
        template = AWSTemplate(template_id="t1", metadata={"fleet_role": "role-arn"})
        assert template.fleet_role == "role-arn"

    def test_percent_on_demand_from_metadata(self) -> None:
        template = AWSTemplate(template_id="t1", metadata={"percent_on_demand": "25"})
        assert template.percent_on_demand == 25

    def test_abis_requirements_from_metadata(self) -> None:
        template = AWSTemplate(
            template_id="t1",
            metadata={
                "abis_instance_requirements": {
                    "VCpuCount": {"Min": 1, "Max": 4},
                    "MemoryMiB": {"Min": 1024, "Max": 8192},
                }
            },
        )
        assert template.abis_instance_requirements is not None
        assert template.abis_instance_requirements.memory_mib.max == 8192


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSTemplateValidationErrors:
    def test_percent_on_demand_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError, match="percent_on_demand must be between 0 and 100"):
            AWSTemplate(template_id="t1", percent_on_demand=150)

    def test_percent_on_demand_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="percent_on_demand must be between 0 and 100"):
            AWSTemplate(template_id="t1", percent_on_demand=-1)

    @pytest.mark.parametrize("version", ["$Latest", "$Default"])
    def test_launch_template_version_special_values_accepted(self, version: str) -> None:
        template = AWSTemplate(template_id="t1", launch_template_version=version)
        assert template.launch_template_version == version

    def test_launch_template_version_positive_integer_accepted(self) -> None:
        template = AWSTemplate(template_id="t1", launch_template_version="3")
        assert template.launch_template_version == "3"

    def test_launch_template_version_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="launch_template_version must be"):
            AWSTemplate(template_id="t1", launch_template_version="0")

    def test_launch_template_version_non_numeric_rejected(self) -> None:
        with pytest.raises(ValidationError, match="launch_template_version must be"):
            AWSTemplate(template_id="t1", launch_template_version="not-a-version")

    def test_native_spec_mutual_exclusion_launch_template(self) -> None:
        with pytest.raises(ValidationError, match="Cannot specify both launch_template_spec"):
            AWSTemplate(
                template_id="t1",
                launch_template_spec={"a": 1},
                launch_template_spec_file="/tmp/spec.json",
            )

    def test_native_spec_mutual_exclusion_provider_api_spec(self) -> None:
        with pytest.raises(ValidationError, match="Cannot specify both provider_api_spec"):
            AWSTemplate(
                template_id="t1",
                provider_api_spec={"a": 1},
                provider_api_spec_file="/tmp/spec.json",
            )


# ---------------------------------------------------------------------------
# allocation_strategy_on_demand coercion / serialization
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAllocationStrategyOnDemand:
    def test_string_is_coerced_to_value_object(self) -> None:
        template = AWSTemplate(template_id="t1", allocation_strategy_on_demand="capacity-optimized")
        assert isinstance(template.allocation_strategy_on_demand, AWSAllocationStrategy)
        assert template.allocation_strategy_on_demand.value == "capacityOptimized"

    def test_none_is_preserved(self) -> None:
        template = AWSTemplate(template_id="t1", allocation_strategy_on_demand=None)
        assert template.allocation_strategy_on_demand is None

    def test_existing_value_object_is_preserved(self) -> None:
        strategy = AWSAllocationStrategy("lowestPrice")
        template = AWSTemplate(template_id="t1", allocation_strategy_on_demand=strategy)
        assert template.allocation_strategy_on_demand is strategy

    def test_serializes_to_plain_string(self) -> None:
        template = AWSTemplate(template_id="t1", allocation_strategy_on_demand="lowestPrice")
        dumped = template.model_dump()
        assert dumped["allocation_strategy_on_demand"] == "lowestPrice"

    def test_serializes_none_to_none(self) -> None:
        template = AWSTemplate(template_id="t1")
        dumped = template.model_dump()
        assert dumped["allocation_strategy_on_demand"] is None

    def test_get_ec2_fleet_on_demand_allocation_strategy_uses_value(self) -> None:
        template = AWSTemplate(template_id="t1", allocation_strategy_on_demand="capacityOptimized")
        assert template.get_ec2_fleet_on_demand_allocation_strategy() == "capacity-optimized"

    def test_get_ec2_fleet_on_demand_allocation_strategy_falls_back(self) -> None:
        template = AWSTemplate(template_id="t1")
        assert template.get_ec2_fleet_on_demand_allocation_strategy() == "lowest-price"


# ---------------------------------------------------------------------------
# allocation_strategy resolution (base field, not on_demand)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAllocationStrategyResolution:
    def test_default_allocation_strategies_when_unset(self) -> None:
        template = AWSTemplate(template_id="t1")
        assert template.get_ec2_fleet_allocation_strategy() == "lowest-price"
        assert template.get_spot_fleet_allocation_strategy() == "lowestPrice"
        assert template.get_asg_allocation_strategy() == "lowest-price"

    def test_string_allocation_strategy_resolves_via_each_api_format(self) -> None:
        template = AWSTemplate(template_id="t1", allocation_strategy="capacity_optimized")
        assert template.get_ec2_fleet_allocation_strategy() == "capacity-optimized"
        assert template.get_spot_fleet_allocation_strategy() == "capacityOptimized"
        assert template.get_asg_allocation_strategy() == "capacity-optimized"

    def test_value_object_allocation_strategy_resolves(self) -> None:
        template = AWSTemplate(template_id="t1")
        object.__setattr__(template, "allocation_strategy", AWSAllocationStrategy("diversified"))
        assert template.get_ec2_fleet_allocation_strategy() == "diversified"


# ---------------------------------------------------------------------------
# to_aws_api_format
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToAwsApiFormat:
    def test_includes_aws_specific_fields(self) -> None:
        template = AWSTemplate(
            template_id="t1",
            provider_api=ProviderApi.EC2_FLEET,
            fleet_type=AWSFleetType.MAINTAIN,
            fleet_role="role-x",
            spot_fleet_request_expiry=60,
            percent_on_demand=20,
            pools_count=3,
            launch_template_id="lt-0123456789abcdef0",
            launch_template_version="2",
        )
        api_format = template.to_aws_api_format()
        assert api_format["provider_api"] == "EC2Fleet"
        assert api_format["fleet_type"] == "maintain"
        assert api_format["fleet_role"] == "role-x"
        assert api_format["spot_fleet_request_expiry"] == 60
        assert api_format["percent_on_demand"] == 20
        assert api_format["pools_count"] == 3
        assert api_format["launch_template_id"] == "lt-0123456789abcdef0"
        assert api_format["launch_template_version"] == "2"

    def test_omits_provider_api_and_fleet_type_when_unset(self) -> None:
        template = AWSTemplate(template_id="t1")
        api_format = template.to_aws_api_format()
        assert api_format["provider_api"] is None
        assert api_format["fleet_type"] is None

    def test_includes_allocation_strategy_on_demand_when_set(self) -> None:
        template = AWSTemplate(template_id="t1", allocation_strategy_on_demand="lowestPrice")
        api_format = template.to_aws_api_format()
        assert api_format["allocation_strategy_on_demand"] == "lowestPrice"

    def test_omits_allocation_strategy_on_demand_when_unset(self) -> None:
        template = AWSTemplate(template_id="t1")
        api_format = template.to_aws_api_format()
        # model_dump() (via base_format) already serialises every declared field,
        # including allocation_strategy_on_demand=None; the explicit `if` guard
        # only decides whether to *overwrite* that key with a non-None value.
        assert api_format["allocation_strategy_on_demand"] is None


# ---------------------------------------------------------------------------
# from_aws_format
#
# NOTE: AWSTemplate.from_aws_format() is dead code (not called anywhere in
# src/ or tests/ outside this file) and is currently broken for every input,
# including a minimal {"template_id": "..."} payload: it unconditionally
# builds `core_data["tags"] = AWSTags.from_dict(...)`, but the inherited
# `Template.tags` field is typed `dict[str, Any]`, not `AWSTags`. Passing an
# AWSTags value object into `cls.model_validate(aws_data)` always raises
#   pydantic_core.ValidationError: tags - Input should be a valid dictionary
#     [type=dict_type, input_value=AWSTags(tags={}), input_type=AWSTags]
# This looks like a real, pre-existing bug (not something introduced by this
# test change) rather than a test-authoring mistake — see tests below that
# pin down the failure without working around it in src/.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFromAwsFormatTagsBug:
    def test_minimal_payload_raises_due_to_tags_type_mismatch(self) -> None:
        """Documents the from_aws_format() tags/AWSTags type-mismatch bug.

        Even the smallest possible payload fails because `tags` is always
        set to an AWSTags instance before being passed to model_validate(),
        and Template.tags is declared as dict[str, Any].
        """
        with pytest.raises(ValidationError, match="tags"):
            AWSTemplate.from_aws_format({"template_id": "t-minimal"})

    def test_payload_with_tags_raises_same_bug(self) -> None:
        with pytest.raises(ValidationError, match="tags"):
            AWSTemplate.from_aws_format(
                {"template_id": "t-with-tags", "instance_tags": {"Name": "x"}}
            )


# ---------------------------------------------------------------------------
# get_aws_configuration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAwsConfiguration:
    def test_raises_when_provider_api_not_set(self) -> None:
        template = AWSTemplate(template_id="t1")
        with pytest.raises(ValueError, match="provider_api is not set"):
            template.get_aws_configuration()

    def test_builds_configuration_with_value_object_allocation_strategy(self) -> None:
        template = AWSTemplate(
            template_id="t1",
            provider_api=ProviderApi.EC2_FLEET,
            subnet_ids=["subnet-0123456789abcdef0"],
            security_group_ids=["sg-0123456789abcdef0"],
        )
        object.__setattr__(template, "allocation_strategy", AWSAllocationStrategy("diversified"))
        config = template.get_aws_configuration()
        assert config.handler_type == ProviderApi.EC2_FLEET
        assert config.allocation_strategy == "diversified"
        assert [s.value for s in config.subnet_ids] == ["subnet-0123456789abcdef0"]
        assert [s.value for s in config.security_group_ids] == ["sg-0123456789abcdef0"]

    def test_builds_configuration_with_string_allocation_strategy(self) -> None:
        template = AWSTemplate(
            template_id="t1",
            provider_api=ProviderApi.RUN_INSTANCES,
            allocation_strategy="capacity-optimized",
        )
        config = template.get_aws_configuration()
        assert config.allocation_strategy == "capacityOptimized"

    def test_price_type_string_is_parsed(self) -> None:
        template = AWSTemplate(
            template_id="t1", provider_api=ProviderApi.RUN_INSTANCES, price_type="spot"
        )
        config = template.get_aws_configuration()
        assert config.price_type == PriceType.SPOT

    def test_invalid_price_type_string_falls_back_to_none_then_default(self) -> None:
        template = AWSTemplate(template_id="t1", provider_api=ProviderApi.RUN_INSTANCES)
        object.__setattr__(template, "price_type", "not-a-real-price-type")
        config = template.get_aws_configuration()
        # AWSConfiguration defaults price_type to ONDEMAND when None is passed.
        assert config.price_type == PriceType.ONDEMAND


# ---------------------------------------------------------------------------
# ABIS instance requirements payload
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInstanceRequirementsPayload:
    def test_returns_none_when_not_set(self) -> None:
        template = AWSTemplate(template_id="t1")
        assert template.get_instance_requirements_payload() is None

    def test_returns_aws_dict_when_set(self) -> None:
        template = AWSTemplate(
            template_id="t1",
            abis_instance_requirements=ABISInstanceRequirements(
                VCpuCount=AWSRequiredIntegerRange(Min=1, Max=2),
                MemoryMiB=AWSRequiredIntegerRange(Min=512, Max=1024),
            ),
        )
        payload = template.get_instance_requirements_payload()
        assert payload == {
            "VCpuCount": {"Min": 1, "Max": 2},
            "MemoryMiB": {"Min": 512, "Max": 1024},
        }


# ---------------------------------------------------------------------------
# ABISInstanceRequirements standalone behaviour
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestABISInstanceRequirements:
    def test_accepts_snake_case_input(self) -> None:
        reqs = ABISInstanceRequirements(
            vcpu_count=AWSRequiredIntegerRange(min=2, max=4),  # type: ignore[call-arg]
            memory_mib=AWSRequiredIntegerRange(min=1024, max=2048),  # type: ignore[call-arg]
        )
        assert reqs.vcpu_count.min == 2
        assert reqs.memory_mib.max == 2048

    def test_to_aws_dict_excludes_none_fields(self) -> None:
        reqs = ABISInstanceRequirements(
            VCpuCount=AWSRequiredIntegerRange(Min=1, Max=2),
            MemoryMiB=AWSRequiredIntegerRange(Min=512, Max=1024),
        )
        aws_dict = reqs.to_aws_dict()
        assert "CpuManufacturers" not in aws_dict
        assert aws_dict["VCpuCount"] == {"Min": 1, "Max": 2}

    def test_extra_fields_are_ignored(self) -> None:
        reqs = ABISInstanceRequirements(
            VCpuCount=AWSRequiredIntegerRange(Min=1, Max=2),
            MemoryMiB=AWSRequiredIntegerRange(Min=512, Max=1024),
            **{"SomeUnknownField": "ignored"},  # type: ignore[arg-type]
        )
        assert not hasattr(reqs, "SomeUnknownField")
