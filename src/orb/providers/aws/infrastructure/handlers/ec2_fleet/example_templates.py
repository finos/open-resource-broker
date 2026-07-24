"""Static example templates for the EC2 Fleet handler.

This module holds the catalogue of example :class:`AWSTemplate` instances
that cover every combination of fleet type × price type.  Keeping them
here as a module-level constant (built once on first import) removes ~140
lines of static data from the handler and makes the examples independently
importable and testable.
"""

from __future__ import annotations

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.value_objects import AWSAllocationStrategy


def build_ec2_fleet_example_templates() -> list[AWSTemplate]:
    """Build the list of example EC2 Fleet templates.

    Returns a new list on each call; callers that want a cached copy should
    store the result themselves (the handler uses ``get_example_templates``
    which returns the module-level constant).
    """
    return [
        # Instant fleet types
        AWSTemplate(
            template_id="EC2Fleet-Instant-OnDemand",
            name="EC2 Fleet Instant On-Demand",
            description="EC2 Fleet with instant fulfillment using on-demand instances",
            provider_api="EC2Fleet",
            machine_types={"t3.medium": 2, "t3.xlarge": 4},
            max_machines=100,
            price_type="ondemand",
            subnet_ids=[],
            security_group_ids=[],
            tags={"Environment": "dev"},
            metadata={"fleet_type": "instant"},
        ),
        AWSTemplate(
            template_id="EC2Fleet-Instant-Spot",
            name="EC2 Fleet Instant Spot",
            description="EC2 Fleet with instant fulfillment using spot instances",
            provider_api="EC2Fleet",
            machine_types={"t3.medium": 2, "t3.xlarge": 4},
            max_machines=100,
            price_type="spot",
            max_price=0.10,
            subnet_ids=[],
            security_group_ids=[],
            tags={"Environment": "dev"},
            metadata={"fleet_type": "instant"},
        ),
        AWSTemplate(
            template_id="EC2Fleet-Instant-Mixed",
            name="EC2 Fleet Instant Mixed",
            description="EC2 Fleet with instant fulfillment using mixed pricing",
            provider_api="EC2Fleet",
            machine_types={"t3.medium": 2, "t3.xlarge": 4},
            max_machines=100,
            price_type="heterogeneous",
            percent_on_demand=30,
            allocation_strategy="diversified",
            max_price=0.10,
            subnet_ids=[],
            security_group_ids=[],
            tags={"Environment": "dev"},
            metadata={"fleet_type": "instant", "percent_on_demand": 30},
        ),
        # Request fleet types
        AWSTemplate(
            template_id="EC2Fleet-Request-OnDemand",
            name="EC2 Fleet Request On-Demand",
            description="EC2 Fleet with request fulfillment using on-demand instances",
            provider_api="EC2Fleet",
            machine_types={"t3.medium": 2, "t3.xlarge": 4},
            max_machines=100,
            price_type="ondemand",
            subnet_ids=[],
            security_group_ids=[],
            tags={"Environment": "test"},
            metadata={"fleet_type": "request"},
        ),
        AWSTemplate(
            template_id="EC2Fleet-Request-Spot",
            name="EC2 Fleet Request Spot",
            description="EC2 Fleet with request fulfillment using spot instances",
            provider_api="EC2Fleet",
            machine_types={"t3.medium": 2, "t3.xlarge": 4},
            max_machines=100,
            price_type="spot",
            allocation_strategy="capacityOptimized",
            max_price=0.10,
            subnet_ids=[],
            security_group_ids=[],
            tags={"Environment": "test"},
            metadata={"fleet_type": "request"},
        ),
        AWSTemplate(
            template_id="EC2Fleet-Request-Mixed",
            name="EC2 Fleet Request Mixed",
            description="EC2 Fleet with request fulfillment using mixed pricing",
            provider_api="EC2Fleet",
            machine_types={"t3.medium": 2, "t3.large": 2, "t3.xlarge": 4},
            max_machines=100,
            price_type="heterogeneous",
            percent_on_demand=40,
            allocation_strategy="diversified",
            allocation_strategy_on_demand=AWSAllocationStrategy.from_string("lowestPrice"),
            max_price=0.10,
            subnet_ids=[],
            security_group_ids=[],
            tags={"Environment": "test"},
            metadata={"fleet_type": "request", "percent_on_demand": 40},
        ),
        # Maintain fleet types
        AWSTemplate(
            template_id="EC2Fleet-Maintain-OnDemand",
            name="EC2 Fleet Maintain On-Demand",
            description="EC2 Fleet with maintain capacity using on-demand instances",
            provider_api="EC2Fleet",
            machine_types={"t3.medium": 2, "t3.xlarge": 4},
            max_machines=100,
            price_type="ondemand",
            subnet_ids=[],
            security_group_ids=[],
            tags={"Environment": "prod"},
            metadata={"fleet_type": "maintain"},
        ),
        AWSTemplate(
            template_id="EC2Fleet-Maintain-Spot",
            name="EC2 Fleet Maintain Spot",
            description="EC2 Fleet with maintain capacity using spot instances",
            provider_api="EC2Fleet",
            machine_types={"t3.medium": 2, "t3.xlarge": 4},
            max_machines=100,
            price_type="spot",
            allocation_strategy="priceCapacityOptimized",
            max_price=0.10,
            subnet_ids=[],
            security_group_ids=[],
            tags={"Environment": "prod"},
            metadata={"fleet_type": "maintain"},
        ),
        AWSTemplate(
            template_id="EC2Fleet-Maintain-Mixed",
            name="EC2 Fleet Maintain Mixed",
            description="EC2 Fleet with maintain capacity using mixed pricing",
            provider_api="EC2Fleet",
            machine_types={"t3.medium": 2, "t3.large": 2, "t3.xlarge": 4},
            max_machines=100,
            price_type="heterogeneous",
            percent_on_demand=50,
            allocation_strategy="capacityOptimized",
            allocation_strategy_on_demand=AWSAllocationStrategy.from_string("prioritized"),
            max_price=0.10,
            subnet_ids=[],
            security_group_ids=[],
            tags={"Environment": "prod"},
            metadata={"fleet_type": "maintain", "percent_on_demand": 50},
        ),
    ]


#: Module-level constant: the full catalogue of EC2 Fleet example templates.
#: Built once on import; ``EC2FleetHandler.get_example_templates`` returns this list.
EC2_FLEET_EXAMPLE_TEMPLATES: list[AWSTemplate] = build_ec2_fleet_example_templates()
