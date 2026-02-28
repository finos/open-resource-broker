"""Unit tests for module-level allocation strategy mapping functions."""

import pytest

from providers.aws.infrastructure.handlers.fleet_override_builder import (
    map_ec2_fleet_allocation_strategy,
    map_ec2_fleet_ondemand_strategy,
    map_spot_fleet_allocation_strategy,
)


class TestMapEc2FleetAllocationStrategy:
    @pytest.mark.parametrize(
        "input_strategy, expected",
        [
            ("capacityOptimized", "capacity-optimized"),
            ("capacityOptimizedPrioritized", "capacity-optimized-prioritized"),
            ("diversified", "diversified"),
            ("lowestPrice", "lowest-price"),
            ("priceCapacityOptimized", "price-capacity-optimized"),
        ],
    )
    def test_known_strategies(self, input_strategy: str, expected: str) -> None:
        assert map_ec2_fleet_allocation_strategy(input_strategy) == expected

    def test_unknown_strategy_defaults_to_lowest_price(self) -> None:
        assert map_ec2_fleet_allocation_strategy("unknownStrategy") == "lowest-price"

    def test_empty_string_defaults_to_lowest_price(self) -> None:
        assert map_ec2_fleet_allocation_strategy("") == "lowest-price"


class TestMapEc2FleetOndemandStrategy:
    @pytest.mark.parametrize(
        "input_strategy, expected",
        [
            ("lowestPrice", "lowest-price"),
            ("prioritized", "prioritized"),
        ],
    )
    def test_known_strategies(self, input_strategy: str, expected: str) -> None:
        assert map_ec2_fleet_ondemand_strategy(input_strategy) == expected

    def test_unknown_strategy_defaults_to_lowest_price(self) -> None:
        assert map_ec2_fleet_ondemand_strategy("unknownStrategy") == "lowest-price"

    def test_empty_string_defaults_to_lowest_price(self) -> None:
        assert map_ec2_fleet_ondemand_strategy("") == "lowest-price"


class TestMapSpotFleetAllocationStrategy:
    @pytest.mark.parametrize(
        "input_strategy, expected",
        [
            ("capacityOptimized", "capacityOptimized"),
            ("capacityOptimizedPrioritized", "capacityOptimizedPrioritized"),
            ("diversified", "diversified"),
            ("lowestPrice", "lowestPrice"),
            ("priceCapacityOptimized", "priceCapacityOptimized"),
        ],
    )
    def test_known_strategies(self, input_strategy: str, expected: str) -> None:
        assert map_spot_fleet_allocation_strategy(input_strategy) == expected

    def test_unknown_strategy_defaults_to_lowest_price(self) -> None:
        assert map_spot_fleet_allocation_strategy("unknownStrategy") == "lowestPrice"

    def test_empty_string_defaults_to_lowest_price(self) -> None:
        assert map_spot_fleet_allocation_strategy("") == "lowestPrice"

    def test_ec2_fleet_and_spot_fleet_use_different_formats(self) -> None:
        # EC2 Fleet uses hyphenated; Spot Fleet uses camelCase
        assert map_ec2_fleet_allocation_strategy("capacityOptimized") == "capacity-optimized"
        assert map_spot_fleet_allocation_strategy("capacityOptimized") == "capacityOptimized"
