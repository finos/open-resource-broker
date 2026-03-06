"""AWS Infrastructure Handlers - Organized AWS resource handlers."""

from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler

__all__: list[str] = [
    "ASGHandler",
    "AWSHandler",
    "EC2FleetHandler",
    "RunInstancesHandler",
    "SpotFleetHandler",
]
