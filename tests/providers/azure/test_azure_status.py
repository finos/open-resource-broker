"""Unit tests for the shared Azure VM power-state mapping."""

from types import SimpleNamespace

from orb.providers.azure.infrastructure.handlers.azure_status import (
    resolve_power_state,
    resolve_raw_power_state_code,
)


def _status(code: str) -> SimpleNamespace:
    return SimpleNamespace(code=code, time=None)


def test_resolve_power_state_maps_stopped_and_deallocated_to_the_same_domain_status():
    """Both billed 'stopped' and unbilled 'deallocated' collapse to domain 'stopped'.

    HostFactory/MachineStatus has no "deallocated" state, so both Azure
    power states must map to the same domain value -- callers that need the
    distinction read it from resolve_raw_power_state_code instead.
    """
    assert resolve_power_state([_status("PowerState/stopped")]) == "stopped"
    assert resolve_power_state([_status("PowerState/deallocated")]) == "stopped"


def test_resolve_raw_power_state_code_distinguishes_stopped_from_deallocated():
    assert resolve_raw_power_state_code([_status("PowerState/stopped")]) == "PowerState/stopped"
    assert (
        resolve_raw_power_state_code([_status("PowerState/deallocated")])
        == "PowerState/deallocated"
    )


def test_resolve_raw_power_state_code_returns_none_without_a_power_state():
    assert resolve_raw_power_state_code([_status("ProvisioningState/succeeded")]) is None
    assert resolve_raw_power_state_code([]) is None
