"""Pin the on-demand/spot split of the Spot Fleet and ASG config builders.

The two builders intentionally differ (Spot Fleet floors a non-zero on-demand
percentage at one instance and ignores percentages for on-demand templates; the
ASG builder lets any percentage win over the price type and defaults to
on-demand). Unifying them changes fleet capacity, so a change must show up here.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.asg.config_builder import ASGConfigBuilder
from orb.providers.aws.infrastructure.handlers.shared.base_context_mixin import BaseContextMixin
from orb.providers.aws.infrastructure.handlers.spot_fleet.config_builder import (
    SpotFleetConfigBuilder,
)

# (price_type, percent_on_demand, count, spot_fleet (on_demand, spot), asg (on_demand, spot))
CASES = [
    ("ondemand", None, 5, (5, 0), (5, 0)),
    ("ondemand", 40, 5, (5, 0), (2, 3)),
    ("spot", None, 5, (0, 5), (0, 5)),
    ("spot", 0, 5, (0, 5), (0, 5)),
    ("spot", 10, 5, (1, 4), (0, 5)),
    ("spot", 50, 4, (2, 2), (2, 2)),
    ("heterogeneous", None, 5, (0, 5), (5, 0)),
    ("heterogeneous", 30, 10, (3, 7), (3, 7)),
]


def _template(price_type: str, percent_on_demand: int | None) -> AWSTemplate:
    fields: dict[str, Any] = dict(
        template_id="tpl-1",
        name="tpl",
        provider_api="SpotFleet",
        machine_type="t3.micro",
        machine_types={"t3.micro": 1},
        machine_image="ami-12345678",
        max_machines=20,
        price_type=price_type,
        subnet_ids=["subnet-1"],
        security_group_ids=["sg-1"],
    )
    if percent_on_demand is not None:
        fields["percent_on_demand"] = percent_on_demand
    return AWSTemplate(**fields)


@pytest.mark.parametrize("price_type,percent,count,spot_expected,asg_expected", CASES)
def test_builders_split_capacity_as_pinned(price_type, percent, count, spot_expected, asg_expected):
    template = _template(price_type, percent)

    spot = SpotFleetConfigBuilder(None, None, MagicMock())._calculate_capacity_distribution(
        template, count
    )
    asg = ASGConfigBuilder(None, None, MagicMock())._calculate_capacity_distribution(
        template, count
    )

    assert (spot["on_demand_count"], spot["spot_count"]) == spot_expected
    assert (asg["on_demand_count"], asg["spot_count"]) == asg_expected


def test_context_mixin_has_no_capacity_calculation():
    assert not hasattr(BaseContextMixin, "_calculate_capacity_distribution")


def test_only_run_instances_handler_uses_context_mixin():
    from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
    from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
    from orb.providers.aws.infrastructure.handlers.run_instances.handler import (
        RunInstancesHandler,
    )
    from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler

    assert issubclass(RunInstancesHandler, BaseContextMixin)
    for handler in (ASGHandler, EC2FleetHandler, SpotFleetHandler):
        assert not issubclass(handler, BaseContextMixin)
