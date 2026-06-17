"""Canonical ARM resource ID parsing helpers for Azure infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ParsedArmResourceId:
    """Validated ARM resource identifier components."""

    subscription_id: str
    resource_group: str
    provider_namespace: str
    resource_path_segments: tuple[str, ...]

    @property
    def resource_name(self) -> str:
        """Return the leaf resource name from the ARM resource path."""
        return self.resource_path_segments[-1]

    @property
    def resource_type(self) -> str:
        """Return the ARM resource type segment (e.g. 'virtualMachines')."""
        return self.resource_path_segments[-2]

    def parent_resource_id(self) -> Optional[str]:
        """Return the parent ARM resource ID for child resources."""
        if len(self.resource_path_segments) <= 2:
            return None

        return (
            f"/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{self.resource_group}"
            f"/providers/{self.provider_namespace}/"
            + "/".join(self.resource_path_segments[:-2])
        )


class ArmResourceIdParser:
    """Parse and project canonical Azure ARM resource identifiers."""

    @staticmethod
    def parse(arm_id: str) -> Optional[ParsedArmResourceId]:
        """Parse an ARM resource ID only when it matches the canonical shape."""
        raw_arm_id = str(arm_id).strip()
        if not raw_arm_id:
            return None

        stripped = raw_arm_id.strip("/")
        if not stripped:
            return None

        parts = stripped.split("/")
        if any(not segment for segment in parts):
            return None

        if len(parts) < 8:
            return None

        if parts[0].lower() != "subscriptions" or parts[2].lower() != "resourcegroups":
            return None

        if parts[4].lower() != "providers":
            return None

        subscription_id = parts[1]
        resource_group = parts[3]
        provider_namespace = parts[5]
        resource_path_segments = tuple(parts[6:])

        if not subscription_id or not resource_group or not provider_namespace:
            return None

        if len(resource_path_segments) < 2 or len(resource_path_segments) % 2 != 0:
            return None

        return ParsedArmResourceId(
            subscription_id=subscription_id,
            resource_group=resource_group,
            provider_namespace=provider_namespace,
            resource_path_segments=resource_path_segments,
        )

    def extract_resource_group_and_name(self, arm_id: str) -> Optional[tuple[str, str]]:
        """Extract ``(resource_group, resource_name)`` from an ARM resource ID."""
        parsed_arm_id = self.parse(arm_id)
        if parsed_arm_id is None:
            return None
        return parsed_arm_id.resource_group, parsed_arm_id.resource_name

    def subnet_id_to_vnet_id(self, subnet_id: Optional[str]) -> Optional[str]:
        """Return the parent VNet ARM ID from a subnet ARM ID."""
        if not subnet_id:
            return None
        parsed_arm_id = self.parse(subnet_id)
        if parsed_arm_id is None:
            return None
        if parsed_arm_id.resource_type.lower() != "subnets":
            return None
        return parsed_arm_id.parent_resource_id()
