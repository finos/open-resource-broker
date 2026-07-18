"""Unit tests for fleet_override_builder pure functions.

Covers all branch paths in build_ec2_fleet_overrides and
build_spot_fleet_overrides without any AWS / network calls.
"""

from __future__ import annotations

import pytest

from orb.providers.aws.infrastructure.handlers.shared.fleet_override_builder import (
    build_ec2_fleet_overrides,
    build_spot_fleet_overrides,
)

# ---------------------------------------------------------------------------
# build_ec2_fleet_overrides
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildEC2FleetOverrides:
    """Tests for build_ec2_fleet_overrides."""

    def test_empty_when_no_args(self):
        result = build_ec2_fleet_overrides(
            machine_types=None,
            machine_types_ondemand=None,
            subnet_ids=None,
            is_heterogeneous=False,
        )
        assert result == []

    def test_subnet_only_produces_subnet_overrides(self):
        """When only subnet_ids is given, entries contain only SubnetId."""
        result = build_ec2_fleet_overrides(
            machine_types=None,
            machine_types_ondemand=None,
            subnet_ids=["subnet-aaa", "subnet-bbb"],
            is_heterogeneous=False,
        )
        assert len(result) == 2
        assert result[0] == {"SubnetId": "subnet-aaa"}
        assert result[1] == {"SubnetId": "subnet-bbb"}

    def test_machine_types_without_subnets(self):
        """machine_types present, no subnets → overrides without SubnetId."""
        result = build_ec2_fleet_overrides(
            machine_types={"m5.xlarge": 2, "c5.xlarge": 1},
            machine_types_ondemand=None,
            subnet_ids=None,
            is_heterogeneous=False,
        )
        for override in result:
            assert "SubnetId" not in override
        instance_types = [o["InstanceType"] for o in result]
        assert set(instance_types) == {"m5.xlarge", "c5.xlarge"}
        weights = {o["InstanceType"]: o["WeightedCapacity"] for o in result}
        assert weights["m5.xlarge"] == 2
        assert weights["c5.xlarge"] == 1

    def test_machine_types_with_subnets_cross_product(self):
        """Each (subnet, instance_type) pair becomes one override entry."""
        result = build_ec2_fleet_overrides(
            machine_types={"m5.xlarge": 4},
            machine_types_ondemand=None,
            subnet_ids=["subnet-1", "subnet-2"],
            is_heterogeneous=False,
        )
        assert len(result) == 2
        assert {o["SubnetId"] for o in result} == {"subnet-1", "subnet-2"}
        for override in result:
            assert override["InstanceType"] == "m5.xlarge"
            assert override["WeightedCapacity"] == 4

    def test_heterogeneous_with_subnets_appends_ondemand(self):
        """is_heterogeneous=True with subnet → ondemand types added per subnet."""
        result = build_ec2_fleet_overrides(
            machine_types={"m5.large": 1},
            machine_types_ondemand={"r5.large": 2},
            subnet_ids=["subnet-x"],
            is_heterogeneous=True,
        )
        # 1 spot + 1 ondemand for single subnet
        assert len(result) == 2
        types = {o["InstanceType"] for o in result}
        assert types == {"m5.large", "r5.large"}

    def test_heterogeneous_without_subnets_appends_ondemand(self):
        """is_heterogeneous=True without subnet → ondemand types appended."""
        result = build_ec2_fleet_overrides(
            machine_types={"m5.large": 1},
            machine_types_ondemand={"r5.large": 2},
            subnet_ids=None,
            is_heterogeneous=True,
        )
        assert len(result) == 2
        types = {o["InstanceType"] for o in result}
        assert types == {"m5.large", "r5.large"}

    def test_not_heterogeneous_ignores_ondemand(self):
        """is_heterogeneous=False → ondemand types are NOT appended."""
        result = build_ec2_fleet_overrides(
            machine_types={"m5.large": 1},
            machine_types_ondemand={"r5.large": 2},
            subnet_ids=None,
            is_heterogeneous=False,
        )
        assert len(result) == 1
        assert result[0]["InstanceType"] == "m5.large"

    def test_priority_ordering_applied(self):
        """Lower priority value appears first in the output list."""
        result = build_ec2_fleet_overrides(
            machine_types={"c5.large": 1, "m5.large": 1, "r5.large": 1},
            machine_types_ondemand=None,
            subnet_ids=None,
            is_heterogeneous=False,
            machine_types_priority={"r5.large": 1, "m5.large": 2, "c5.large": 3},
        )
        instance_types = [o["InstanceType"] for o in result]
        assert instance_types == ["r5.large", "m5.large", "c5.large"]

    def test_unknown_type_in_priority_gets_default_999(self):
        """Types not in priority dict are treated as priority 999 (sorted last)."""
        result = build_ec2_fleet_overrides(
            machine_types={"z99.xlarge": 1, "a1.small": 1},
            machine_types_ondemand=None,
            subnet_ids=None,
            is_heterogeneous=False,
            machine_types_priority={"a1.small": 1},
        )
        # a1.small has priority 1, z99.xlarge gets 999 — a1.small comes first
        assert result[0]["InstanceType"] == "a1.small"
        assert result[1]["InstanceType"] == "z99.xlarge"

    def test_empty_machine_types_dict_treated_as_falsy(self):
        """Empty dict for machine_types falls through to subnet-only branch."""
        result = build_ec2_fleet_overrides(
            machine_types={},
            machine_types_ondemand=None,
            subnet_ids=["subnet-z"],
            is_heterogeneous=False,
        )
        # {} is falsy — falls through to elif subnet_ids branch
        assert len(result) == 1
        assert result[0] == {"SubnetId": "subnet-z"}


# ---------------------------------------------------------------------------
# build_spot_fleet_overrides
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSpotFleetOverrides:
    """Tests for build_spot_fleet_overrides."""

    def test_empty_when_no_args(self):
        result = build_spot_fleet_overrides(
            machine_types=None,
            machine_types_ondemand=None,
            subnet_ids=None,
            max_price=None,
            is_heterogeneous=False,
        )
        assert result == []

    def test_subnet_only_produces_subnet_overrides(self):
        result = build_spot_fleet_overrides(
            machine_types=None,
            machine_types_ondemand=None,
            subnet_ids=["subnet-a", "subnet-b"],
            max_price=None,
            is_heterogeneous=False,
        )
        assert len(result) == 2
        assert {o["SubnetId"] for o in result} == {"subnet-a", "subnet-b"}

    def test_priority_index_set_in_non_heterogeneous_with_subnets(self):
        """Each override gets Priority = 1-based index."""
        result = build_spot_fleet_overrides(
            machine_types={"m5.xlarge": 2, "c5.xlarge": 1},
            machine_types_ondemand=None,
            subnet_ids=["subnet-1"],
            max_price=None,
            is_heterogeneous=False,
        )
        priorities = {o["InstanceType"]: o["Priority"] for o in result}
        # Two types → priorities 1 and 2
        assert set(priorities.values()) == {1, 2}

    def test_spot_price_included_when_max_price_set(self):
        """SpotPrice field added when max_price is provided."""
        result = build_spot_fleet_overrides(
            machine_types={"m5.large": 1},
            machine_types_ondemand=None,
            subnet_ids=["subnet-1"],
            max_price="0.10",
            is_heterogeneous=False,
        )
        assert len(result) == 1
        assert result[0]["SpotPrice"] == "0.10"

    def test_spot_price_excluded_when_max_price_none(self):
        """SpotPrice field absent when max_price is None."""
        result = build_spot_fleet_overrides(
            machine_types={"m5.large": 1},
            machine_types_ondemand=None,
            subnet_ids=["subnet-1"],
            max_price=None,
            is_heterogeneous=False,
        )
        assert "SpotPrice" not in result[0]

    def test_max_price_converted_to_str(self):
        """Numeric max_price is str()-converted before use."""
        result = build_spot_fleet_overrides(
            machine_types={"m5.large": 1},
            machine_types_ondemand=None,
            subnet_ids=None,
            max_price=0.05,
            is_heterogeneous=False,
        )
        assert result[0]["SpotPrice"] == "0.05"

    def test_heterogeneous_with_subnets_appends_ondemand_no_spot_price(self):
        """OnDemand overrides have no SpotPrice even in heterogeneous mode."""
        result = build_spot_fleet_overrides(
            machine_types={"m5.large": 1},
            machine_types_ondemand={"r5.large": 2},
            subnet_ids=["subnet-x"],
            max_price="0.5",
            is_heterogeneous=True,
        )
        spot = [o for o in result if o["InstanceType"] == "m5.large"]
        on_demand = [o for o in result if o["InstanceType"] == "r5.large"]
        assert spot[0]["SpotPrice"] == "0.5"
        assert "SpotPrice" not in on_demand[0]

    def test_heterogeneous_ondemand_priority_offset(self):
        """OnDemand priorities start at len(machine_types) + 1."""
        result = build_spot_fleet_overrides(
            machine_types={"m5.large": 1, "c5.large": 1},
            machine_types_ondemand={"r5.large": 2},
            subnet_ids=["subnet-x"],
            max_price=None,
            is_heterogeneous=True,
        )
        on_demand = [o for o in result if o["InstanceType"] == "r5.large"]
        # len(machine_types) = 2 → ondemand starts at 3
        assert on_demand[0]["Priority"] == 3

    def test_machine_types_without_subnets_no_heterogeneous(self):
        """No subnets, not heterogeneous → simple list with Priority and no SubnetId."""
        result = build_spot_fleet_overrides(
            machine_types={"m5.large": 1},
            machine_types_ondemand=None,
            subnet_ids=None,
            max_price=None,
            is_heterogeneous=False,
        )
        assert len(result) == 1
        assert "SubnetId" not in result[0]
        assert result[0]["Priority"] == 1

    def test_machine_types_without_subnets_heterogeneous(self):
        """No subnets, heterogeneous → both spot and ondemand overrides present."""
        result = build_spot_fleet_overrides(
            machine_types={"m5.large": 1},
            machine_types_ondemand={"r5.large": 1},
            subnet_ids=None,
            max_price=None,
            is_heterogeneous=True,
        )
        types = {o["InstanceType"] for o in result}
        assert types == {"m5.large", "r5.large"}

    def test_priority_ordering_applied_spot(self):
        """Lower priority value appears first in output list."""
        result = build_spot_fleet_overrides(
            machine_types={"z5.large": 1, "a1.small": 1},
            machine_types_ondemand=None,
            subnet_ids=None,
            max_price=None,
            is_heterogeneous=False,
            machine_types_priority={"a1.small": 1, "z5.large": 2},
        )
        instance_types = [o["InstanceType"] for o in result]
        assert instance_types[0] == "a1.small"
        assert instance_types[1] == "z5.large"

    def test_zero_price_treated_as_falsy_no_spot_price_field(self):
        """max_price=0 is falsy → SpotPrice not included."""
        result = build_spot_fleet_overrides(
            machine_types={"m5.large": 1},
            machine_types_ondemand=None,
            subnet_ids=None,
            max_price=0,
            is_heterogeneous=False,
        )
        assert "SpotPrice" not in result[0]
