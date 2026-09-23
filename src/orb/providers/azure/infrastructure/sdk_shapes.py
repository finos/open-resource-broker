"""Shared structural typing helpers for Azure SDK object shapes.

These protocols describe the small attribute subsets ORB consumes from the
Azure SDK models. Keeping them here centralizes the cast boundary for Azure's
generated model types instead of redefining local one-off protocols in each
handler/service.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, cast


class AzureStatusWithCodeProtocol(Protocol):
    """Azure status-like object exposing a ``code`` attribute."""

    @property
    def code(self) -> str | None:
        """Return the Azure status code."""
        ...


class AzureVmWithNameProtocol(Protocol):
    """Azure VM-like object exposing a ``name`` attribute."""

    @property
    def name(self) -> str | None:
        """Return the Azure VM resource name."""
        ...


class AzureVmWithIdentityProtocol(Protocol):
    """Azure VM-like object exposing ``name`` and ``vm_id`` attributes."""

    @property
    def name(self) -> Optional[str]:
        """Return the Azure VM resource name."""
        ...

    @property
    def vm_id(self) -> Optional[str]:
        """Return Azure's stable VM identifier."""
        ...


class AzureVmHardwareProfileProtocol(Protocol):
    """Azure VM hardware profile surface used for status normalization."""

    @property
    def vm_size(self) -> Optional[str]:
        """Return the Azure VM size."""
        ...


class AzureVmRuntimeStatusProtocol(AzureVmWithIdentityProtocol, Protocol):
    """Azure VM surface shared by standalone VMs and VMSS member VMs.

    Azure's SDK exposes standalone ``VirtualMachine`` and VMSS
    ``VirtualMachineScaleSetVM`` as separate generated classes. ORB only needs
    this structural subset when normalizing status.
    """

    @property
    def hardware_profile(self) -> Optional[AzureVmHardwareProfileProtocol]:
        """Return the VM hardware profile when Azure included one."""
        ...

    @property
    def instance_view(self) -> object | None:
        """Return the VM instance view when Azure included one."""
        ...

    @property
    def location(self) -> Optional[str]:
        """Return the Azure location for the VM."""
        ...

    @property
    def zones(self) -> Optional[list[str]]:
        """Return the availability zones attached to the VM."""
        ...

    @property
    def tags(self) -> Optional[dict[str, str]]:
        """Return the user-supplied tag dict applied to the VM, if any."""
        ...


class AzureInstanceViewWithStatusesProtocol(Protocol):
    """Azure instance-view-like object exposing ``statuses``."""

    @property
    def statuses(self) -> list[Any]:
        """Return Azure instance-view status entries."""
        ...


def instance_view_statuses(instance_view: object | None) -> list[Any] | None:
    """Return Azure instance-view statuses when the object shape supports them."""
    if instance_view is None:
        return None
    return cast(AzureInstanceViewWithStatusesProtocol, instance_view).statuses
