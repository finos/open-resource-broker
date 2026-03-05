"""Unit tests for allocation strategy mapping via AWSTemplate methods."""

import pytest

from providers.aws.domain.template.aws_template_aggregate import AWSTemplate


def _make_template(allocation_strategy: str, **kwargs) -> AWSTemplate:
    return AWSTemplate(
        template_id="test",
        name="test",
        provider_api="EC2Fleet",
        allocation_strategy=allocation_strategy,
        subnet_ids=[],
        security_group_ids=[],
        **kwargs,
    )


class TestGetEc2FleetAllocationStrategy:
    @pytest.mark.parametrize(
        "input_strategy, expected",
        [
            ("capacity_optimized", "capacity-optimized"),
            ("capacity_optimized_prioritized", "capacity-optimized-prioritized"),
            ("diversified", "diversified"),
            ("lowest_price", "lowest-price"),
            ("price_capacity_optimized", "price-capacity-optimized"),
            ("capacityOptimized", "capacity-optimized"),
            ("capacityOptimizedPrioritized", "capacity-optimized-prioritized"),
            ("lowestPrice", "lowest-price"),
            ("priceCapacityOptimized", "price-capacity-optimized"),
        ],
    )
    def test_known_strategies(self, input_strategy: str, expected: str) -> None:
        template = _make_template(input_strategy)
        assert template.get_ec2_fleet_allocation_strategy() == expected

    def test_no_strategy_defaults_to_lowest_price(self) -> None:
        template = _make_template("")
        assert template.get_ec2_fleet_allocation_strategy() == "lowest-price"


class TestGetEc2FleetOnDemandAllocationStrategy:
    @pytest.mark.parametrize(
        "input_strategy, expected",
        [
            ("lowest_price", "lowest-price"),
            ("prioritized", "prioritized"),
            ("lowestPrice", "lowest-price"),
        ],
    )
    def test_known_strategies(self, input_strategy: str, expected: str) -> None:
        template = _make_template("lowest_price", allocation_strategy_on_demand=input_strategy)
        assert template.get_ec2_fleet_on_demand_allocation_strategy() == expected

    def test_falls_back_to_spot_strategy_when_no_ondemand(self) -> None:
        template = _make_template("capacity_optimized")
        assert template.get_ec2_fleet_on_demand_allocation_strategy() == "capacity-optimized"


class TestGetSpotFleetAllocationStrategy:
    @pytest.mark.parametrize(
        "input_strategy, expected",
        [
            ("capacity_optimized", "capacityOptimized"),
            ("capacity_optimized_prioritized", "capacityOptimizedPrioritized"),
            ("diversified", "diversified"),
            ("lowest_price", "lowestPrice"),
            ("price_capacity_optimized", "priceCapacityOptimized"),
            ("capacityOptimized", "capacityOptimized"),
            ("lowestPrice", "lowestPrice"),
        ],
    )
    def test_known_strategies(self, input_strategy: str, expected: str) -> None:
        template = _make_template(input_strategy)
        assert template.get_spot_fleet_allocation_strategy() == expected

    def test_no_strategy_defaults_to_lowest_price(self) -> None:
        template = _make_template("")
        assert template.get_spot_fleet_allocation_strategy() == "lowestPrice"

    def test_ec2_fleet_and_spot_fleet_use_different_formats(self) -> None:
        template = _make_template("capacity_optimized")
        assert template.get_ec2_fleet_allocation_strategy() == "capacity-optimized"
        assert template.get_spot_fleet_allocation_strategy() == "capacityOptimized"
