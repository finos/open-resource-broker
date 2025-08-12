"""Machine-specific value objects orchestrator.

This module provides an integrated interface to all machine value objects organized by category:
- Machine status (MachineStatus)
- Machine identifiers and core types (MachineId, MachineType)
- Machine metadata and configuration (PriceType, MachineConfiguration, MachineEvent, HealthCheck, etc.)
"""

from .machine_identifiers import MachineId, MachineType
from .machine_metadata import (
    HealthCheck,
    HealthCheckResult,
    IPAddressRange,
    MachineConfiguration,
    MachineEvent,
    MachineMetadata,
    PriceType,
    ResourceTag,
)

# Import all value objects from specialized modules
from .machine_status import MachineStatus

# Export all value objects
__all__ = [
    # Machine status
    "MachineStatus",
    # Machine identifiers and core types
    "MachineId",
    "MachineType",
    # Machine metadata and configuration
    "PriceType",
    "MachineConfiguration",
    "MachineEvent",
    "HealthCheck",
    "IPAddressRange",
    "MachineMetadata",
    "HealthCheckResult",
    "ResourceTag",
]
