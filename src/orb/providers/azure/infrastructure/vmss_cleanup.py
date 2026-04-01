"""Typed Azure-owned follow-up state and coordination for VMSS empty-delete cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable, Mapping, Optional, Protocol, TypeAlias, Any


class _VmssCleanupLogger(Protocol):
    """Protocol for logging warnings in the VMSS cleanup coordinator."""
    def warning(self, msg: str, *args: object) -> None:
        """A minimal logger protocol for the VMSS cleanup coordinator, focused on warning messages."""
        ...

GetVmssMemberCount: TypeAlias = Callable[..., Optional[int]]
VmssExists: TypeAlias = Callable[..., Optional[bool]]
BeginDeleteVmss: TypeAlias = Callable[..., None]


@dataclass
class PendingVmssCleanup:
    """Durable cleanup intent and submission state for one VMSS."""

    resource_group: str
    vmss_name: str
    machine_ids: list[str]
    delete_vmss_when_empty: bool
    delete_submitted: bool = False
    delete_retry_pending: bool = False
    last_delete_error: Optional[str] = None
    delete_submission_semantics: str = "best_effort_without_reverification"

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, object]) -> Optional[PendingVmssCleanup]:
        """Create a PendingVmssCleanup instance from a metadata mapping.

        Args:
            metadata (Mapping[str, object]): Metadata containing cleanup info.
        Returns:
            Optional[PendingVmssCleanup]: The constructed instance, or None if invalid.
        """
        resource_group = metadata.get("resource_group")
        vmss_name = metadata.get("vmss_name")
        if vmss_name in (None, ""):
            vmss_name = metadata.get("resource_id")
        raw_machine_ids = metadata.get("machine_ids", [])
        if resource_group in (None, "") or vmss_name in (None, ""):
            return None
        if not isinstance(raw_machine_ids, list):
            return None

        machine_ids: list[str] = []
        for machine_id in raw_machine_ids:
            machine_id_str = str(machine_id)
            if machine_id_str and machine_id_str not in machine_ids:
                machine_ids.append(machine_id_str)

        return cls(
            resource_group=str(resource_group),
            vmss_name=str(vmss_name),
            machine_ids=machine_ids,
            delete_vmss_when_empty=bool(metadata.get("delete_vmss_when_empty", False)),
            delete_submitted=bool(metadata.get("delete_submitted", False)),
            delete_retry_pending=bool(metadata.get("delete_retry_pending", False)),
            last_delete_error=(
                None
                if metadata.get("last_delete_error") in (None, "")
                else str(metadata.get("last_delete_error"))
            ),
            delete_submission_semantics=str(
                metadata.get("delete_submission_semantics", "best_effort_without_reverification")
            ),
        )

    @classmethod
    def create(
        cls,
        *,
        resource_group: str,
        vmss_name: str,
        machine_ids: list[str],
        delete_vmss_when_empty: bool,
        delete_submitted: bool = False,
        delete_retry_pending: bool = False,
        last_delete_error: Optional[str] = None,
    ) -> PendingVmssCleanup:
        """Create a new PendingVmssCleanup instance with the given parameters.

        Args:
            resource_group (str): The Azure resource group name.
            vmss_name (str): The VMSS name.
            machine_ids (list[str]): List of machine IDs.
            delete_vmss_when_empty (bool): Whether to delete VMSS when empty.
            delete_submitted (bool): Whether delete has been submitted.
            delete_retry_pending (bool): Whether a retry is pending.
            last_delete_error (Optional[str]): Last error message, if any.
        Returns:
            PendingVmssCleanup: The constructed instance.
        """
        return cls(
            resource_group=str(resource_group),
            vmss_name=str(vmss_name),
            machine_ids=[str(machine_id) for machine_id in machine_ids],
            delete_vmss_when_empty=delete_vmss_when_empty,
            delete_submitted=delete_submitted,
            delete_retry_pending=delete_retry_pending,
            last_delete_error=(
                None if last_delete_error in (None, "") else str(last_delete_error)
            ),
        )

    def combine_for_same_vmss(self, other: PendingVmssCleanup) -> PendingVmssCleanup:
        """Merge this cleanup with another for the same VMSS, combining machine IDs and state.

        Args:
            other (PendingVmssCleanup): Another cleanup for the same VMSS.
        Returns:
            PendingVmssCleanup: The merged cleanup instance.
        """
        merged_machine_ids = list(self.machine_ids)
        for machine_id in other.machine_ids:
            if machine_id not in merged_machine_ids:
                merged_machine_ids.append(machine_id)

        return PendingVmssCleanup(
            resource_group=self.resource_group,
            vmss_name=self.vmss_name,
            machine_ids=merged_machine_ids,
            delete_vmss_when_empty=self.delete_vmss_when_empty or other.delete_vmss_when_empty,
            delete_submitted=self.delete_submitted or other.delete_submitted,
            delete_retry_pending=self.delete_retry_pending or other.delete_retry_pending,
            last_delete_error=other.last_delete_error or self.last_delete_error,
            delete_submission_semantics=other.delete_submission_semantics
            or self.delete_submission_semantics,
        )

    def mark_delete_submitted(self) -> None:
        """Mark this cleanup as having had its delete submitted."""
        self.delete_submitted = True
        self.delete_retry_pending = False
        self.last_delete_error = None

    def mark_delete_retry_pending(self, exc: Exception) -> None:
        """Mark this cleanup as needing a retry, recording the exception message.

        Args:
            exc (Exception): The exception that caused the retry.
        """
        self.delete_submitted = False
        self.delete_retry_pending = True
        self.last_delete_error = str(exc)

    def to_metadata(self) -> dict[str, Any]:
        """Convert this cleanup to a metadata dictionary for serialization.

        Returns:
            dict[str, Any]: Metadata representing this cleanup.
        """
        metadata: dict[str, object] = {
            "resource_group": self.resource_group,
            "vmss_name": self.vmss_name,
            "machine_ids": list(self.machine_ids),
            "delete_vmss_when_empty": self.delete_vmss_when_empty,
            "delete_submission_semantics": self.delete_submission_semantics,
            "delete_submitted": self.delete_submitted,
            "delete_retry_pending": self.delete_retry_pending,
        }
        if self.last_delete_error not in (None, ""):
            metadata["last_delete_error"] = self.last_delete_error
        return metadata

    def to_status_detail(self) -> dict[str, Any]:
        """Get a status detail dictionary for reporting purposes.

        Returns:
            dict[str, Any]: Status detail for this cleanup.
        """
        return self.to_metadata()


class VmssCleanupCoordinator:
    """Azure-owned coordinator for VMSS empty-delete follow-up and retries."""

    def __init__(
        self,
        *,
        logger: _VmssCleanupLogger,
        get_vmss_member_count: GetVmssMemberCount,
        vmss_exists: VmssExists,
        begin_delete_vmss: BeginDeleteVmss,
    ) -> None:
        """Initialize the coordinator with necessary dependencies."""
        self._logger = logger
        self._get_vmss_member_count = get_vmss_member_count
        self._vmss_exists = vmss_exists
        self._begin_delete_vmss = begin_delete_vmss
        self._pending_cleanups: dict[tuple[str, str], PendingVmssCleanup] = {}
        self._lock = RLock()

    def clear(self) -> None:
        """Clear all pending VMSS cleanups."""
        with self._lock:
            self._pending_cleanups.clear()

    def record(self, handler_result: Mapping[str, object] | object) -> None:
        """Record a pending cleanup from a handler result, if present.

        Args:
            handler_result (Mapping[str, object] | object): Handler result possibly containing cleanup info.
        """
        if not isinstance(handler_result, Mapping):
            return

        provider_data = handler_result.get("provider_data")
        if not isinstance(provider_data, Mapping):
            return

        pending_metadata = provider_data.get("pending_resource_cleanup")
        if not isinstance(pending_metadata, Mapping):
            return

        pending = PendingVmssCleanup.from_metadata(pending_metadata)
        if pending is None:
            return

        key = (pending.resource_group, pending.vmss_name)
        with self._lock:
            existing = self._pending_cleanups.get(key)
            self._pending_cleanups[key] = (
                pending if existing is None else existing.combine_for_same_vmss(pending)
            )

    def restore_from_request_metadata(self, request_metadata: Mapping[str, object]) -> None:
        """Restore pending cleanups from request metadata.

        Args:
            request_metadata (Mapping[str, object]): Metadata possibly containing cleanup info.
        """
        direct_pending = request_metadata.get("pending_resource_cleanup")
        if isinstance(direct_pending, Mapping):
            self.record({"provider_data": request_metadata})

        termination_requests = request_metadata.get("termination_requests")
        if not isinstance(termination_requests, list):
            return

        for termination_request in termination_requests:
            if isinstance(termination_request, Mapping):
                self.record({"provider_data": termination_request})

    def has_pending(self, *, resource_group: Optional[str], resource_ids: list[str]) -> bool:
        """Check if there are pending cleanups for the given resource group and IDs.

        Args:
            resource_group (Optional[str]): The Azure resource group name.
            resource_ids (list[str]): List of resource IDs.
        Returns:
            bool: True if any pending cleanup exists, False otherwise.
        """
        if not resource_group:
            return False

        with self._lock:
            for resource_id in resource_ids:
                if (str(resource_group), str(resource_id)) in self._pending_cleanups:
                    return True
            return False

    def status_metadata(
        self,
        *,
        resource_group: Optional[str],
        resource_ids: list[str],
    ) -> dict[str, Any]:
        """Get metadata about pending cleanups for reporting.

        Args:
            resource_group (Optional[str]): The Azure resource group name.
            resource_ids (list[str]): List of resource IDs.
        Returns:
            dict[str, Any]: Metadata about pending cleanups.
        """
        follow_up_details: list[dict[str, Any]] = []

        if resource_group:
            with self._lock:
                for resource_id in self._dedupe_resource_ids(resource_ids):
                    pending = self._pending_cleanups.get((str(resource_group), str(resource_id)))
                    if pending is not None:
                        follow_up_details.append(pending.to_status_detail())

        return {
            "termination_follow_up_pending": bool(follow_up_details),
            "termination_follow_up_details": follow_up_details,
        }

    def reconcile(
        self,
        *,
        resource_group: Optional[str],
        resource_ids: list[str],
        observed_ids: set[str],
    ) -> None:
        """Reconcile pending cleanups with observed instance IDs, submitting deletes if needed.

        Args:
            resource_group (Optional[str]): The Azure resource group name.
            resource_ids (list[str]): List of resource IDs.
            observed_ids (set[str]): Set of observed instance IDs.
        """
        if not resource_group or not resource_ids:
            return

        for vmss_name in self._dedupe_resource_ids(resource_ids):
            self._reconcile_one(
                resource_group=str(resource_group),
                vmss_name=vmss_name,
                observed_ids=observed_ids,
            )

    @staticmethod
    def _dedupe_resource_ids(resource_ids: list[str]) -> list[str]:
        """Remove duplicates from a list of resource IDs, preserving order.

        Args:
            resource_ids (list[str]): List of resource IDs.
        Returns:
            list[str]: Deduplicated list of resource IDs.
        """
        deduped: list[str] = []
        for resource_id in resource_ids:
            vmss_name = str(resource_id)
            if vmss_name and vmss_name not in deduped:
                deduped.append(vmss_name)
        return deduped

    def _reconcile_one(
        self,
        *,
        resource_group: str,
        vmss_name: str,
        observed_ids: set[str],
    ) -> None:
        """Reconcile cleanup for a single VMSS, submitting delete if empty and not already submitted.

        Args:
            resource_group (str): The Azure resource group name.
            vmss_name (str): The VMSS name.
            observed_ids (set[str]): Set of observed instance IDs.
        """
        key = (resource_group, vmss_name)
        with self._lock:
            pending = self._pending_cleanups.get(key)
        if pending is None:
            return

        requested_ids = set(pending.machine_ids)
        if not requested_ids:
            with self._lock:
                self._pending_cleanups.pop(key, None)
            return

        if pending.delete_submitted:
            self._clear_if_vmss_is_gone(resource_group=resource_group, vmss_name=vmss_name)
            return

        if requested_ids & observed_ids:
            return

        try:
            if self._submit_vmss_delete_if_empty(key=key, pending=pending):
                return
            with self._lock:
                self._pending_cleanups.pop(key, None)
        except Exception as exc:
            with self._lock:
                current = self._pending_cleanups.get(key)
                if current is not None:
                    current.mark_delete_retry_pending(exc)
            self._logger.warning(
                "Failed to clean up pending VMSS '%s' in '%s': %s",
                vmss_name,
                resource_group,
                exc,
            )

    def _clear_if_vmss_is_gone(self, *, resource_group: str, vmss_name: str) -> None:
        """Remove pending cleanup if the VMSS no longer exists in Azure.

        Args:
            resource_group (str): The Azure resource group name.
            vmss_name (str): The VMSS name.
        """
        if self._vmss_exists(resource_group=resource_group, vmss_name=vmss_name) is False:
            with self._lock:
                self._pending_cleanups.pop((resource_group, vmss_name), None)

    def _submit_vmss_delete_if_empty(
        self,
        *,
        key: tuple[str, str],
        pending: PendingVmssCleanup,
    ) -> bool:
        """Submit a VMSS delete if it is empty and deletion is required.

        Args:
            key (tuple[str, str]): The (resource_group, vmss_name) key.
            pending (PendingVmssCleanup): The pending cleanup object.
        Returns:
            bool: True if delete was submitted or still pending, False if not needed.
        """
        if not pending.delete_vmss_when_empty:
            return False

        member_count = self._get_vmss_member_count(
            resource_group=pending.resource_group,
            vmss_name=pending.vmss_name,
        )
        if member_count is None or member_count > 0:
            return True

        with self._lock:
            current = self._pending_cleanups.get(key)
            if current is None:
                return False
            if current.delete_submitted:
                return True
            current.mark_delete_submitted()

        try:
            self._begin_delete_vmss(
                resource_group=pending.resource_group,
                vmss_name=pending.vmss_name,
            )
            return True
        except Exception as exc:
            with self._lock:
                current = self._pending_cleanups.get(key)
                if current is not None:
                    current.mark_delete_retry_pending(exc)
            raise


