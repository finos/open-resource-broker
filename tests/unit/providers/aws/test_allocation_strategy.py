"""Tests for AWS allocation strategy normalisation and formatting."""

import pytest

from providers.aws.domain.template.value_objects import (
    AWSAllocationStrategy,
    normalise_allocation_strategy,
)

# ---------------------------------------------------------------------------
# normalise_allocation_strategy
# ---------------------------------------------------------------------------


class TestNormaliseAllocationStrategy:
    """Unit tests for the normalise_allocation_strategy function."""

    @pytest.mark.parametrize(
        "canonical",
        [
            "capacityOptimized",
            "capacityOptimizedPrioritized",
            "diversified",
            "lowestPrice",
            "priceCapacityOptimized",
            "prioritized",
        ],
    )
    def test_canonical_camel_case_passes_through_unchanged(self, canonical: str) -> None:
        assert normalise_allocation_strategy(canonical) == canonical

    @pytest.mark.parametrize(
        "hyphenated, expected",
        [
            ("capacity-optimized", "capacityOptimized"),
            ("capacity-optimized-prioritized", "capacityOptimizedPrioritized"),
            ("lowest-price", "lowestPrice"),
            ("price-capacity-optimized", "priceCapacityOptimized"),
        ],
    )
    def test_hyphenated_normalises_to_camel_case(self, hyphenated: str, expected: str) -> None:
        assert normalise_allocation_strategy(hyphenated) == expected

    @pytest.mark.parametrize(
        "snake, expected",
        [
            ("capacity_optimized", "capacityOptimized"),
            ("capacity_optimized_prioritized", "capacityOptimizedPrioritized"),
            ("lowest_price", "lowestPrice"),
            ("price_capacity_optimized", "priceCapacityOptimized"),
        ],
    )
    def test_snake_case_normalises_to_camel_case(self, snake: str, expected: str) -> None:
        assert normalise_allocation_strategy(snake) == expected

    def test_unknown_value_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown allocation strategy"):
            normalise_allocation_strategy("not-a-real-strategy")


# ---------------------------------------------------------------------------
# AWSAllocationStrategy output formatters
# ---------------------------------------------------------------------------


class TestAWSAllocationStrategyFormatters:
    """Tests for to_ec2_fleet_format, to_spot_fleet_format, to_asg_format."""

    @pytest.mark.parametrize(
        "canonical, expected",
        [
            ("capacityOptimized", "capacity-optimized"),
            ("capacityOptimizedPrioritized", "capacity-optimized-prioritized"),
            ("diversified", "diversified"),
            ("lowestPrice", "lowest-price"),
            ("priceCapacityOptimized", "price-capacity-optimized"),
            ("prioritized", "prioritized"),
        ],
    )
    def test_to_ec2_fleet_format_returns_hyphenated(self, canonical: str, expected: str) -> None:
        assert AWSAllocationStrategy(canonical).to_ec2_fleet_format() == expected

    @pytest.mark.parametrize(
        "canonical, expected",
        [
            ("capacityOptimized", "capacityOptimized"),
            ("capacityOptimizedPrioritized", "capacityOptimizedPrioritized"),
            ("diversified", "diversified"),
            ("lowestPrice", "lowestPrice"),
            ("priceCapacityOptimized", "priceCapacityOptimized"),
        ],
    )
    def test_to_spot_fleet_format_returns_camel_case(self, canonical: str, expected: str) -> None:
        assert AWSAllocationStrategy(canonical).to_spot_fleet_format() == expected

    @pytest.mark.parametrize(
        "canonical, expected",
        [
            ("capacityOptimized", "capacity-optimized"),
            ("capacityOptimizedPrioritized", "capacity-optimized-prioritized"),
            ("diversified", "diversified"),
            ("lowestPrice", "lowest-price"),
            ("priceCapacityOptimized", "price-capacity-optimized"),
        ],
    )
    def test_to_asg_format_returns_hyphenated(self, canonical: str, expected: str) -> None:
        assert AWSAllocationStrategy(canonical).to_asg_format() == expected

    @pytest.mark.parametrize(
        "input_value",
        [
            "capacityOptimized",
            "capacity-optimized",
            "capacity_optimized",
        ],
    )
    def test_from_string_accepts_all_formats(self, input_value: str) -> None:
        strategy = AWSAllocationStrategy.from_string(input_value)
        assert strategy.value == "capacityOptimized"


# ---------------------------------------------------------------------------
# AWSAllocationStrategy construction from any format
# ---------------------------------------------------------------------------


class TestAWSAllocationStrategyConstruction:
    """Tests that all three input formats produce the same canonical .value."""

    @pytest.mark.parametrize(
        "camel, hyphenated, snake",
        [
            ("capacityOptimized", "capacity-optimized", "capacity_optimized"),
            (
                "capacityOptimizedPrioritized",
                "capacity-optimized-prioritized",
                "capacity_optimized_prioritized",
            ),
            ("lowestPrice", "lowest-price", "lowest_price"),
            ("priceCapacityOptimized", "price-capacity-optimized", "price_capacity_optimized"),
        ],
    )
    def test_all_formats_produce_same_canonical_value(
        self, camel: str, hyphenated: str, snake: str
    ) -> None:
        assert AWSAllocationStrategy(camel).value == camel
        assert AWSAllocationStrategy(hyphenated).value == camel
        assert AWSAllocationStrategy(snake).value == camel

    def test_from_core_with_enum_like_object(self) -> None:
        class _FakeEnum:
            value = "capacity-optimized"

        strategy = AWSAllocationStrategy.from_core(_FakeEnum())
        assert strategy.value == "capacityOptimized"

    def test_from_core_with_plain_string(self) -> None:
        # from_core falls back to str(strategy) when no .value attribute
        strategy = AWSAllocationStrategy.from_core("lowest-price")  # type: ignore[arg-type]
        assert strategy.value == "lowestPrice"


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestAWSAllocationStrategyRoundTrip:
    """Round-trip tests: input format → construction → output format."""

    def test_camel_case_input_to_ec2_fleet_format(self) -> None:
        assert (
            AWSAllocationStrategy("capacityOptimized").to_ec2_fleet_format() == "capacity-optimized"
        )

    def test_hyphenated_input_to_spot_fleet_format(self) -> None:
        assert (
            AWSAllocationStrategy("capacity-optimized").to_spot_fleet_format()
            == "capacityOptimized"
        )

    def test_snake_case_input_to_asg_format(self) -> None:
        assert AWSAllocationStrategy("capacity_optimized").to_asg_format() == "capacity-optimized"

    @pytest.mark.parametrize(
        "raw_input, expected_ec2, expected_spot, expected_asg",
        [
            (
                "price-capacity-optimized",
                "price-capacity-optimized",
                "priceCapacityOptimized",
                "price-capacity-optimized",
            ),
            (
                "priceCapacityOptimized",
                "price-capacity-optimized",
                "priceCapacityOptimized",
                "price-capacity-optimized",
            ),
            (
                "price_capacity_optimized",
                "price-capacity-optimized",
                "priceCapacityOptimized",
                "price-capacity-optimized",
            ),
        ],
    )
    def test_all_formats_round_trip_consistently(
        self,
        raw_input: str,
        expected_ec2: str,
        expected_spot: str,
        expected_asg: str,
    ) -> None:
        s = AWSAllocationStrategy(raw_input)
        assert s.to_ec2_fleet_format() == expected_ec2
        assert s.to_spot_fleet_format() == expected_spot
        assert s.to_asg_format() == expected_asg
