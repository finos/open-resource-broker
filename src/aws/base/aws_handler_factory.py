from typing import Dict
from aws.ec2_fleet.ec2_fleet_handler import EC2FleetHandler
from aws.spot_fleet.spot_fleet_handler import SpotFleetHandler
from aws.asg.asg_handler import ASGHandler
from aws.run_insstances.run_instances_handler import RunInstancesHandler
from src.config.provider_config_manager import ProviderConfigManager
from src.helpers.logger import setup_logging

logger = setup_logging()


class AWSHandlerFactory:
    """
    Factory class to create and manage AWS service-specific handlers dynamically based on configuration.
    """

    @staticmethod
    def create_aws_handler(handler_type: str):
        """
        Create an AWS handler based on the specified handler type.

        :param handler_type: The type of AWS handler to create (e.g., "EC2Fleet", "SpotFleet").
        :return: An instance of the corresponding AWS handler.
        :raises ValueError: If the handler type is not recognized.
        """
        config = ProviderConfigManager.get_config()
        region = config.AWS_REGION

        handlers: Dict[str, object] = {
            "EC2Fleet": EC2FleetHandler(region),
            "SpotFleet": SpotFleetHandler(region),
            "ASG": ASGHandler(region),
            "RunInstances": RunInstancesHandler(region),
        }

        if handler_type not in handlers:
            raise ValueError(f"Unsupported AWS handler type: {handler_type}")

        return handlers[handler_type]
