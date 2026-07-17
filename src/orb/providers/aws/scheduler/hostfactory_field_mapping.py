"""AWS implementation of FieldMappingPort for the HostFactory scheduler."""

from orb.infrastructure.scheduler.hostfactory.field_mapping_port import FieldMappingPort


class AWSFieldMapping:
    """AWS-specific field-mapping adapter for the HostFactory scheduler.

    Registers as ``"aws"`` in ``FieldMappingRegistry`` during provider
    bootstrap (``providers/aws/registration.py``).
    """

    # AWS-specific HF field → internal field mappings.
    # Generic mappings live in HostFactoryFieldMappings.MAPPINGS["generic"].
    _PROVIDER_MAPPINGS: dict[str, str] = {
        # AWS VPC network configuration
        "subnetId": "subnet_ids",
        "subnetIds": "subnet_ids",
        "securityGroupIds": "security_group_ids",
        # AWS instance type configurations
        "vmTypesOnDemand": "machine_types_ondemand",
        "vmTypesPriority": "machine_types_priority",
        "abisInstanceRequirements": "abis_instance_requirements",
        # AWS pricing configurations
        "percentOnDemand": "percent_on_demand",
        "allocationStrategyOnDemand": "allocation_strategy_on_demand",
        # AWS fleet configurations
        "fleetRole": "fleet_role",
        "spotFleetRequestExpiry": "spot_fleet_request_expiry",
        "poolsCount": "pools_count",
        # AWS instance configuration
        "instanceProfile": "machine_role",
        "launchTemplateId": "launch_template_id",
        "userDataScript": "user_data",
        # AWS MicroVM configuration
        "imageVersion": "image_version",
        "executionRoleArn": "execution_role_arn",
        "idlePolicy": "idle_policy",
        "maximumDurationInSeconds": "maximum_duration_in_seconds",
        "ingressNetworkConnectors": "ingress_network_connectors",
        "egressNetworkConnectors": "egress_network_connectors",
        "runHookPayload": "run_hook_payload",
    }

    def get_mappings(self) -> dict[str, str]:
        """Return AWS-specific HF-field → internal-field name entries."""
        return dict(self._PROVIDER_MAPPINGS)

    def apply_defaults(self, mapped: dict) -> dict:
        """Apply AWS-specific setdefault logic after field mapping.

        When ``launch_template_id`` is set the launch template already encodes
        network configuration, so ``subnet_ids`` and ``security_group_ids``
        should not be defaulted to empty lists.
        """
        mapped.setdefault("max_instances", 1)
        mapped.setdefault("price_type", "ondemand")
        mapped.setdefault("allocation_strategy", "lowestPrice")
        if not mapped.get("launch_template_id"):
            mapped.setdefault("subnet_ids", [])
            mapped.setdefault("security_group_ids", [])
        mapped.setdefault("tags", {})
        return mapped

    def derive_attributes(self, machine_type: str | None) -> dict[str, list[str]] | None:
        """Build the HF ``attributes`` object from an EC2 instance type string.

        Returns ``None`` when *machine_type* is ``None`` or empty so callers
        can fall back gracefully.
        """
        if not machine_type:
            return None

        from orb.providers.aws.utilities.ec2.instances import derive_cpu_ram_from_instance_type

        ncpus, nram = derive_cpu_ram_from_instance_type(machine_type)
        return {
            "type": ["String", "X86_64"],
            "ncpus": ["Numeric", str(ncpus)],
            "nram": ["Numeric", str(nram)],
        }


# Verify the class satisfies the protocol at import time.
_: FieldMappingPort = AWSFieldMapping()  # type: ignore[assignment]
