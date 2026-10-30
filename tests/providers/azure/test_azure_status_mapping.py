"""Azure power and provisioning states map to values the domain accepts."""

from types import SimpleNamespace

import pytest

from orb.domain.machine.machine_status import MachineStatus
from orb.providers.azure.infrastructure.handlers.azure_status import (
    AZURE_VM_STATE_MAP,
    resolve_power_state,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("PowerState/starting", "pending"),
        ("PowerState/running", "running"),
        ("PowerState/stopping", "stopping"),
        ("PowerState/stopped", "stopped"),
        ("PowerState/deallocating", "stopping"),
        ("PowerState/deallocated", "stopped"),
        ("ProvisioningState/creating", "pending"),
        ("ProvisioningState/succeeded", "running"),
        ("ProvisioningState/failed", "failed"),
        ("ProvisioningState/deleting", "shutting-down"),
    ],
)
def test_state_map_entry(code: str, expected: str) -> None:
    assert resolve_power_state([SimpleNamespace(code=code)]) == expected  # type: ignore[list-item]


@pytest.mark.unit
def test_every_mapped_state_is_a_domain_status() -> None:
    for value in AZURE_VM_STATE_MAP.values():
        assert MachineStatus(value)
