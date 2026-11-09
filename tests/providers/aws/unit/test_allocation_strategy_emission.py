"""Allocation-strategy options are emitted per pool type, the same on the legacy and spec paths.

SpotOptions only appear when the fleet has spot pools and OnDemandOptions only
when it has on-demand pools. Spot Fleet sends OnDemandAllocationStrategy when it
requests on-demand capacity, and ASG sends both strategies in its instances
distribution. A strategy with no wire value for an API raises instead of being
swapped for another one, and a template that no longer validates fails the
request instead of launching a bare template.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.value_objects import AWSAllocationStrategy
from tests.providers.aws.template_defaults_support import fleet_payloads, strategy_options

pytestmark = pytest.mark.unit

_BASE: dict[str, Any] = {
    "template_id": "t",
    "machine_types": {"t3.micro": 1, "t3.small": 1},
    "subnet_ids": ["subnet-1"],
    "security_group_ids": ["sg-1"],
    "fleet_role": "arn:aws:iam::123456789012:role/spot-fleet",
    "allocation_strategy": "priceCapacityOptimized",
    "allocation_strategy_on_demand": "prioritized",
}


def _template(price_type: str, **extra: Any) -> AWSTemplate:
    fields = {**_BASE, "price_type": price_type, **extra}
    if price_type == "heterogeneous":
        fields.setdefault("percent_on_demand", 50)
    return AWSTemplate(**fields)


@pytest.mark.parametrize("price_type", ["ondemand", "spot", "heterogeneous"])
def test_legacy_and_spec_emit_the_same_strategy_options(price_type: str) -> None:
    for api, (legacy, rendered) in fleet_payloads(_template(price_type)).items():
        assert strategy_options(api, legacy) == strategy_options(api, rendered), api


def test_on_demand_ec2_fleet_has_no_spot_options() -> None:
    legacy, rendered = fleet_payloads(_template("ondemand"))["EC2Fleet"]
    for payload in (legacy, rendered):
        assert "SpotOptions" not in payload
        assert payload["OnDemandOptions"] == {"AllocationStrategy": "prioritized"}


def test_spot_ec2_fleet_has_no_on_demand_options() -> None:
    legacy, rendered = fleet_payloads(_template("spot"))["EC2Fleet"]
    for payload in (legacy, rendered):
        assert payload["SpotOptions"] == {"AllocationStrategy": "price-capacity-optimized"}
        assert "OnDemandOptions" not in payload


def test_spot_fleet_sends_the_on_demand_strategy_with_on_demand_capacity() -> None:
    for price_type, expected in (
        ("heterogeneous", "prioritized"),
        ("ondemand", "prioritized"),
        ("spot", None),
    ):
        legacy, rendered = fleet_payloads(_template(price_type))["SpotFleet"]
        for payload in (legacy, rendered):
            assert payload.get("OnDemandAllocationStrategy") == expected, price_type
            assert payload["AllocationStrategy"] == "priceCapacityOptimized"


@pytest.mark.parametrize("price_type", ["spot", "heterogeneous"])
def test_asg_sends_both_strategies_in_legacy_and_spec(price_type: str) -> None:
    legacy, rendered = fleet_payloads(_template(price_type))["ASG"]
    for payload in (legacy, rendered):
        distribution = payload["MixedInstancesPolicy"]["InstancesDistribution"]
        assert distribution["SpotAllocationStrategy"] == "price-capacity-optimized"
        assert distribution["OnDemandAllocationStrategy"] == "prioritized"


def test_max_price_alone_still_sets_spot_options() -> None:
    template = _template("spot", allocation_strategy=None, max_price=0.5)
    legacy, rendered = fleet_payloads(template)["EC2Fleet"]
    for payload in (legacy, rendered):
        assert payload["SpotOptions"] == {"MaxTotalPrice": "0.5"}


def test_unmapped_strategy_raises_instead_of_being_swapped() -> None:
    strategy = AWSAllocationStrategy("lowestPrice")
    with pytest.raises(ValueError, match="Unknown AWS API"):
        strategy.to_api_format("nope")
    object.__setattr__(strategy, "_canonical", "made-up")
    with pytest.raises(ValueError, match="not supported by ec2_fleet"):
        strategy.to_api_format("ec2_fleet")


@pytest.mark.parametrize("api", ["ec2_fleet", "spot_fleet", "asg"])
def test_prioritized_has_a_wire_value_for_on_demand_pools(api: str) -> None:
    assert AWSAllocationStrategy("prioritized").to_api_format(api) == "prioritized"


@pytest.mark.asyncio
async def test_invalid_template_fails_the_request_instead_of_launching_a_bare_template() -> None:
    from orb.providers.aws.services.instance_operation_service import (
        AWSInstanceOperationService,
    )
    from orb.providers.base.strategy import ProviderOperation, ProviderOperationType

    handler = MagicMock()
    service = AWSInstanceOperationService(
        aws_client=MagicMock(),
        logger=MagicMock(),
        provisioning_adapter=MagicMock(),
        machine_adapter=MagicMock(),
    )
    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={
            "template_config": {
                "template_id": "t",
                "provider_api": "EC2Fleet",
                "allocation_strategy": "bogus",
            },
            "count": 1,
        },
    )

    result = await service.create_instances(operation, {"EC2Fleet": handler})

    assert result.success is False
    assert result.error_code == "INVALID_TEMPLATE"
    handler.acquire_hosts.assert_not_called()
    service._provisioning_adapter.provision_resources.assert_not_called()  # type: ignore[attr-defined]


def test_config_with_the_removed_extension_keys_still_loads() -> None:
    from orb.config.schemas.provider_strategy_schema import ProviderDefaults

    defaults = ProviderDefaults.model_validate(
        {
            "template_defaults": {"provider_api": "EC2Fleet"},
            "extensions": {
                "allocation_strategy": "capacityOptimized",
                "allocation_strategy_on_demand": "lowestPrice",
            },
        }
    )
    assert defaults.extensions["allocation_strategy"] == "capacityOptimized"
