"""On-demand strategies written in ``allocation_strategy`` are migrated during default resolution.

Templates written before the spot/on-demand split put the on-demand strategy in
``allocation_strategy``. Template default resolution, the one merge site every
load and GetTemplate use, moves such a value to ``allocation_strategy_on_demand``
through the rule the AWS provider registers, and warns. The rule is idempotent
for GetTemplate's second resolve of the template DTO.
"""

from __future__ import annotations

import warnings
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.domain.template.value_objects import migrate_legacy_on_demand_strategy
from tests.providers.aws.template_defaults_support import make_defaults_service

pytestmark = pytest.mark.unit


def _factory() -> TemplateFactory:
    factory = TemplateFactory()
    factory.register_provider_template_class("aws", AWSTemplate)
    return factory


def _resolve(row: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    service = make_defaults_service(**kwargs)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return service.resolve_template_defaults(
            {"template_id": "legacy", "provider_type": "aws", **row}, "aws-default"
        )


def _strategies(resolved: dict[str, Any]) -> tuple[Any, Any]:
    template = _factory().create_template(resolved)
    assert isinstance(template, AWSTemplate)
    on_demand = template.allocation_strategy_on_demand
    return template.allocation_strategy, on_demand.value if on_demand else None


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        # on-demand-only value, any price type
        (
            {"price_type": "ondemand", "allocation_strategy": "prioritized"},
            ("priceCapacityOptimized", "prioritized"),
        ),
        (
            {"price_type": "spot", "allocation_strategy": "prioritized"},
            ("priceCapacityOptimized", "prioritized"),
        ),
        # lowestPrice on an on-demand template
        (
            {"price_type": "ondemand", "allocation_strategy": "lowest-price"},
            ("priceCapacityOptimized", "lowestPrice"),
        ),
        # lowestPrice on spot or heterogeneous stays the spot strategy
        (
            {"price_type": "spot", "allocation_strategy": "lowestPrice"},
            ("lowestPrice", "lowestPrice"),
        ),
        (
            {"price_type": "heterogeneous", "allocation_strategy": "lowestPrice"},
            ("lowestPrice", "lowestPrice"),
        ),
        # a row that sets the on-demand strategy is not migrated
        (
            {
                "price_type": "ondemand",
                "allocation_strategy": "lowestPrice",
                "allocation_strategy_on_demand": "prioritized",
            },
            ("lowestPrice", "prioritized"),
        ),
    ],
)
def test_legacy_rows_under_the_bundled_defaults(row: dict[str, Any], expected: tuple) -> None:
    assert _strategies(_resolve(row)) == expected


def test_legacy_row_migrates_without_a_type_layer_on_demand_default() -> None:
    type_defaults = {"provider_api": "EC2Fleet", "allocation_strategy": "priceCapacityOptimized"}
    resolved = _resolve(
        {"price_type": "ondemand", "allocation_strategy": "prioritized"},
        type_defaults=type_defaults,
    )
    assert _strategies(resolved) == ("priceCapacityOptimized", "prioritized")


def test_spot_strategy_is_removed_when_no_lower_layer_sets_one() -> None:
    resolved = _resolve(
        {"price_type": "ondemand", "allocation_strategy": "prioritized"},
        type_defaults={"provider_api": "EC2Fleet"},
    )
    assert "allocation_strategy" not in resolved
    assert resolved["allocation_strategy_on_demand"] == "prioritized"


def test_migration_logs_and_warns_once() -> None:
    logger = MagicMock()
    service = make_defaults_service(logger=logger)
    with pytest.warns(DeprecationWarning, match="allocation_strategy_on_demand"):
        service.resolve_template_defaults(
            {"template_id": "legacy", "provider_type": "aws", "allocation_strategy": "prioritized"}
        )
    events = [call.kwargs.get("extra", {}).get("event") for call in logger.warning.call_args_list]
    assert events.count("template.legacy_on_demand_strategy") == 1


@pytest.mark.parametrize(
    ("row", "service_kwargs"),
    [
        ({"price_type": "ondemand", "allocation_strategy": "prioritized"}, {}),
        ({"price_type": "ondemand", "allocation_strategy": "lowestPrice"}, {}),
        ({"price_type": "spot", "allocation_strategy": "lowestPrice"}, {}),
        ({"price_type": "ondemand"}, {}),
        # operator-set spot default lowestPrice with no on-demand default anywhere
        (
            {"price_type": "ondemand"},
            {
                "type_defaults": {"provider_api": "EC2Fleet"},
                "instance_defaults": {"allocation_strategy": "lowestPrice"},
            },
        ),
    ],
)
def test_second_resolve_of_the_dto_gives_the_same_values(
    row: dict[str, Any], service_kwargs: dict[str, Any]
) -> None:
    first = _resolve(row, **service_kwargs)
    dto = TemplateDTOFactory().from_domain(_factory().create_template(first))
    second = _resolve(dto.model_dump(), **service_kwargs)
    assert _strategies(second) == _strategies(first)


def test_rule_ignores_unknown_values() -> None:
    merged = {"allocation_strategy": "bogus", "price_type": "ondemand"}
    result, message = migrate_legacy_on_demand_strategy(
        row={"allocation_strategy": "bogus"}, defaults={}, merged=merged
    )
    assert result is merged
    assert message is None


def test_type_default_on_demand_value_is_not_migrated() -> None:
    """Only row values migrate; an invalid lower-layer value is rejected visibly."""
    resolved = _resolve(
        {"price_type": "ondemand"},
        type_defaults={"provider_api": "EC2Fleet", "allocation_strategy": "prioritized"},
    )
    with pytest.raises(ValueError, match="does not apply to spot pools"):
        _factory().create_template(resolved)
