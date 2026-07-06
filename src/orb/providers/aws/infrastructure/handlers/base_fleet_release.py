"""Base class for fleet release managers.

Captures the shared release-teardown flow used by both EC2Fleet and SpotFleet
release managers so new fleet types (e.g. a hypothetical Spot Block type)
have a tested template to follow without duplicating decision logic.

The flow implemented here:

1. Fetch fleet details if not supplied.
2. Extract fleet_type, target_capacity, and weighted-capacity sum from details.
3. Build a FleetCapacityInput and call compute_fleet_release_decision.
4. Optionally reduce capacity (maintain-type only).
5. Terminate instances.
6. Decide whether to cancel/delete the fleet:
   - Request-type: call _fleet_has_no_remaining_instances as an
     eventual-consistency guard before deleting.
   - Maintain-type: is_full_return=True → delete directly; secondary
     _fleet_has_no_remaining_instances check for weighted-capacity edge case.
   - Instant-type (has_fleet_record=False): always attempt deletion.
7. Clean up the associated ORB launch template.

Parts that differ between fleet types (AWS API shape, API calls, tag keys) are
delegated to abstract methods implemented by each subclass.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.fleet_release_policy import (
    FleetCapacityInput,
    FleetReleaseDecision,
    compute_fleet_release_decision,
)
from orb.providers.aws.utilities.aws_operations import AWSOperations


class BaseFleetReleaseManager(ABC):
    """Abstract base for EC2Fleet and SpotFleet release managers.

    Subclasses implement the AWS-API-specific abstract methods; this class
    owns the shared orchestration flow so the two concrete managers stay thin.
    """

    def __init__(
        self,
        aws_client: AWSClient,
        aws_ops: AWSOperations,
        request_adapter: Optional[RequestAdapterPort],
        cleanup_on_zero_capacity_fn: Callable[[str, str], None],
        logger: LoggingPort,
        retry_fn: Callable[..., Any],
    ) -> None:
        self._aws_client = aws_client
        self._aws_ops = aws_ops
        self._request_adapter = request_adapter
        self._cleanup_on_zero_capacity = cleanup_on_zero_capacity_fn
        self._logger = logger
        self._retry = retry_fn

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def release(
        self,
        fleet_id: str,
        instance_ids: list[str],
        fleet_details: dict[str, Any],
        request_id: str = "",
    ) -> None:
        """Release hosts for a single fleet with proper fleet lifecycle management.

        Args:
            fleet_id: The fleet identifier (EC2 Fleet ID or Spot Fleet request ID).
            instance_ids: Instance IDs to terminate within this fleet.  Pass an
                empty list to cancel/delete the entire fleet unconditionally.
            fleet_details: Pre-fetched fleet description dict, or an empty dict
                to trigger a live fetch via :meth:`_fetch_fleet_details`.
            request_id: ORB request ID used for launch-template cleanup when
                the fleet record is no longer available (instant-fleet case).
        """
        fleet_label = self._fleet_label()
        self._logger.info(
            "Processing %s %s with %d instances", fleet_label, fleet_id, len(instance_ids)
        )

        try:
            if not fleet_details:
                fleet_details = self._fetch_fleet_details(fleet_id)

            capacity_input, extra = self._extract_capacity_input(
                fleet_id, fleet_details, instance_ids
            )

            if instance_ids:
                decision = compute_fleet_release_decision(capacity_input)

                if decision.requires_capacity_reduction:
                    self._reduce_capacity(fleet_id, capacity_input, extra, decision)

                self._terminate_instances(fleet_id, instance_ids)
                self._logger.info(
                    "Terminated %s %s instances: %s", fleet_label, fleet_id, instance_ids
                )

                should_teardown = self._compute_teardown_flag(
                    fleet_id, instance_ids, decision, fleet_label
                )

                self._handle_post_terminate(
                    fleet_id,
                    fleet_details,
                    decision,
                    should_teardown,
                    extra,
                    request_id,
                    fleet_label,
                )
            else:
                # No specific instances — cancel/delete the entire fleet.
                self._cancel_or_delete_fleet(fleet_id, terminate_instances=True)
                self._logger.info("Cancelled entire %s: %s", fleet_label, fleet_id)
                self._cleanup_launch_template(fleet_details, request_id)

        except Exception as exc:
            self._logger.error("Failed to terminate %s %s: %s", fleet_label, fleet_id, exc)
            raise

    # ------------------------------------------------------------------
    # Shared helpers (non-abstract)
    # ------------------------------------------------------------------

    def _compute_teardown_flag(
        self,
        fleet_id: str,
        instance_ids: list[str],
        decision: FleetReleaseDecision,
        fleet_label: str,
    ) -> bool:
        """Determine whether the fleet should be cancelled/deleted after instance termination.

        For request-type fleets (requires_capacity_reduction=False, has_fleet_record=True)
        the _fleet_has_no_remaining_instances guard is consulted to prevent
        stranding running instances due to AWS API eventual-consistency lag.

        For maintain-type fleets the secondary guard is only used when capacity
        arithmetic alone says partial (is_full_return=False) but the fleet may
        be physically empty — a weighted-capacity edge case.

        Instant fleets (has_fleet_record=False) bypass this logic entirely and
        are handled unconditionally in _handle_post_terminate.
        """
        should_teardown = decision.is_full_return

        if (
            should_teardown
            and not decision.requires_capacity_reduction
            and decision.has_fleet_record
        ):
            # Request-type: verify no instances remain before cancelling/deleting.
            should_teardown = self._fleet_has_no_remaining_instances(fleet_id, set(instance_ids))
            if should_teardown:
                self._logger.info(
                    "%s %s has no remaining active instances; cancelling/deleting request-type fleet",
                    fleet_label,
                    fleet_id,
                )
        elif (
            not should_teardown
            and decision.has_fleet_record
            and decision.requires_capacity_reduction
        ):
            # Maintain-type weighted-capacity fallback.
            should_teardown = self._fleet_has_no_remaining_instances(fleet_id, set(instance_ids))
            if should_teardown:
                self._logger.info(
                    "%s %s has no remaining active instances (weighted-capacity case); "
                    "treating as full return",
                    fleet_label,
                    fleet_id,
                )
                self._zero_capacity_before_teardown(fleet_id)

        return should_teardown

    def _handle_post_terminate(
        self,
        fleet_id: str,
        fleet_details: dict[str, Any],
        decision: FleetReleaseDecision,
        should_teardown: bool,
        extra: dict[str, Any],
        request_id: str,
        fleet_label: str,
    ) -> None:
        """Handle fleet-level teardown and launch-template cleanup after instance termination."""
        if should_teardown and decision.has_fleet_record:
            self._logger.info("%s %s is empty, cancelling/deleting fleet", fleet_label, fleet_id)
            # Instances were already terminated above; pass terminate_instances=False.
            # Implementations that need different termination semantics (e.g. EC2Fleet
            # maintain fleets that use TerminateInstances=True as a safety net) handle
            # this distinction inside their _cancel_or_delete_fleet override via is_maintain.
            self._cancel_or_delete_fleet(
                fleet_id,
                terminate_instances=False,
                is_maintain=decision.requires_capacity_reduction,
            )
            self._cleanup_launch_template(fleet_details, request_id)
        elif not decision.has_fleet_record:
            # Instant fleet — AWS may not have auto-deleted the fleet record.
            # Always attempt an explicit delete; swallow errors if already gone.
            try:
                self._cancel_or_delete_fleet(
                    fleet_id,
                    terminate_instances=True,
                    is_maintain=False,
                )
                self._logger.info(
                    "Deleted instant %s %s (instances already terminated)",
                    fleet_label,
                    fleet_id,
                )
            except Exception as exc:
                self._logger.warning(
                    "Could not delete instant %s %s (may already be gone): %s",
                    fleet_label,
                    fleet_id,
                    exc,
                )
            self._cleanup_launch_template(fleet_details, request_id)

    def _zero_capacity_before_teardown(self, fleet_id: str) -> None:
        """Attempt to zero fleet capacity before cancellation to prevent replacement launches.

        Called from _compute_teardown_flag for the maintain-type weighted-capacity
        edge case where is_full_return was False from arithmetic but the live
        describe confirms no instances remain.  Errors are logged as warnings so
        that teardown continues even if the capacity-zero call fails.
        """
        try:
            self._zero_capacity(fleet_id)
        except Exception as exc:
            self._logger.warning(
                "Failed to zero %s %s capacity before cancellation: %s",
                self._fleet_label(),
                fleet_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Abstract methods — implemented by each concrete subclass
    # ------------------------------------------------------------------

    @abstractmethod
    def _fleet_label(self) -> str:
        """Return a human-readable fleet type label for log messages (e.g. "Spot Fleet")."""

    @abstractmethod
    def _fetch_fleet_details(self, fleet_id: str) -> dict[str, Any]:
        """Fetch the fleet description from AWS when not supplied by the caller.

        Returns:
            The raw fleet details dict (the structure varies by fleet type).
        """

    @abstractmethod
    def _extract_capacity_input(
        self,
        fleet_id: str,
        fleet_details: dict[str, Any],
        instance_ids: list[str],
    ) -> tuple[FleetCapacityInput, dict[str, Any]]:
        """Extract fleet_type, capacity numbers, and weighted sum from fleet details.

        Args:
            fleet_id: The fleet identifier; needed for the weighted-capacity
                describe API call to look up per-instance weights.
            fleet_details: Raw fleet description dict.
            instance_ids: Instance IDs being returned.

        Returns:
            A ``(FleetCapacityInput, extra)`` pair where *extra* carries any
            fleet-type-specific data needed by _reduce_capacity or
            _cancel_or_delete_fleet (e.g. on-demand capacity for SpotFleet).
        """

    @abstractmethod
    def _reduce_capacity(
        self,
        fleet_id: str,
        capacity_input: FleetCapacityInput,
        extra: dict[str, Any],
        decision: FleetReleaseDecision,
    ) -> None:
        """Reduce the fleet's target capacity before terminating instances.

        Only called when decision.requires_capacity_reduction is True (maintain
        fleets).  Prevents AWS from launching replacement instances to fill the
        capacity that is about to be freed by instance termination.
        """

    @abstractmethod
    def _terminate_instances(self, fleet_id: str, instance_ids: list[str]) -> None:
        """Terminate the specified instances that belong to this fleet."""

    @abstractmethod
    def _cancel_or_delete_fleet(
        self,
        fleet_id: str,
        terminate_instances: bool,
        is_maintain: bool = False,
    ) -> None:
        """Cancel (SpotFleet) or delete (EC2Fleet) the fleet record.

        Args:
            fleet_id: Fleet identifier.
            terminate_instances: Whether the API call should also terminate any
                remaining instances (True for full-fleet teardown; False when
                instances have already been terminated).
            is_maintain: True when the fleet is a maintain-type fleet.  Some
                implementations use different deletion semantics for maintain
                vs request fleets (e.g. EC2Fleet uses _delete_fleet for maintain
                to get TerminateInstances=True behaviour).
        """

    @abstractmethod
    def _fleet_has_no_remaining_instances(self, fleet_id: str, excluded_ids: set[str]) -> bool:
        """Return True when no active instances outside *excluded_ids* remain in the fleet.

        Implementations must use the appropriate AWS describe call for the fleet
        type.  Should return False on any error (safe default — assume non-empty).

        Args:
            fleet_id: Fleet identifier.
            excluded_ids: Instance IDs already submitted for termination; treat
                these as gone when checking for remaining instances.
        """

    @abstractmethod
    def _zero_capacity(self, fleet_id: str) -> None:
        """Set the fleet's target capacity to zero before cancellation.

        Called for the maintain-type weighted-capacity fallback path to prevent
        AWS from launching replacement instances during the cancellation window.
        """

    @abstractmethod
    def _cleanup_launch_template(
        self,
        fleet_details: dict[str, Any],
        request_id: str = "",
    ) -> None:
        """Delete the ORB-managed launch template associated with this fleet.

        Args:
            fleet_details: Raw fleet description dict.
            request_id: Fallback ORB request ID when the fleet record is
                unavailable or lacks the orb:request-id tag.
        """
