"""Unit tests for ``_outcome_to_provider_result``.

Covers every :class:`OperationOutcome` variant the k8s strategy can produce.
The bridge translates the typed union into the ``ProviderResult`` envelope
that the shared provisioning service consumes; missing or empty fields here
silently break downstream machine creation, so every variant is tested
independently.

Resource-vs-machine model (k8s):

* **Pod handler** — Pod IS its own resource; ``resource_ids == machine_ids``.
* **Deployment / StatefulSet / Job handler** — the controller is the
  resource (1 entry); the Pods it spawns are the machines (N entries).
  ``machine_ids`` is empty at acquire time and populated by the status
  resolver as pods schedule.
"""

from __future__ import annotations

from orb.domain.base.follow_up_context import (
    DeploymentPollingFollowUpContext,
    TerminationFollowUpContext,
)
from orb.domain.base.operation_outcome import (
    Accepted,
    Completed,
    Failed,
    RequiresFollowUp,
)
from orb.providers.k8s.strategy.k8s_provider_strategy import (
    _outcome_to_provider_result,
)

# ---------------------------------------------------------------------------
# Failed
# ---------------------------------------------------------------------------


class TestFailedOutcome:
    def test_failed_sets_success_false_and_error_code(self) -> None:
        outcome = Failed(error="cluster unreachable", recoverable=True)
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert result.success is False
        assert result.error_code == "OPERATION_FAILED"

    def test_failed_metadata_includes_recoverable_flag(self) -> None:
        outcome = Failed(error="boom", recoverable=False, metadata={"foo": "bar"})
        result = _outcome_to_provider_result(outcome, fallback_operation="get_instance_status")
        assert result.metadata["recoverable"] is False
        assert result.metadata["foo"] == "bar"
        assert result.metadata["operation"] == "get_instance_status"
        assert result.metadata["provider"] == "k8s"


# ---------------------------------------------------------------------------
# Accepted
# ---------------------------------------------------------------------------


class TestAcceptedOutcome:
    def test_pod_handler_accept_resource_equals_machine(self) -> None:
        """For bare Pods, resource_ids and machine_ids are the same pod names."""
        outcome = Accepted(
            request_id="req-abc",
            pending_resource_ids=["orb-aaa-0000", "orb-aaa-0001"],
            metadata={
                "machine_ids": ["orb-aaa-0000", "orb-aaa-0001"],
                "provider_api": "Pod",
                "pod_names": ["orb-aaa-0000", "orb-aaa-0001"],
            },
        )
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert result.success is True
        assert result.data["resource_ids"] == ["orb-aaa-0000", "orb-aaa-0001"]
        assert result.data["instance_ids"] == ["orb-aaa-0000", "orb-aaa-0001"]
        # Pod handler does not pre-populate instance dicts at acquire time;
        # the status resolver fills them in on the first poll.
        assert result.data["instances"] == []
        assert result.data["tracking_request_id"] == "req-abc"

    def test_deployment_handler_accept_resource_is_controller_machines_empty(self) -> None:
        """For Deployments, resource_ids = [deployment_name], machine_ids = []."""
        outcome = Accepted(
            request_id="req-dep",
            pending_resource_ids=["deploy-orb-xyz"],
            metadata={
                "machine_ids": [],
                "provider_api": "Deployment",
                "deployment_name": "deploy-orb-xyz",
            },
        )
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert result.data["resource_ids"] == ["deploy-orb-xyz"]
        # Pods haven't been scheduled yet — no machine rows materialised.
        assert result.data["instances"] == []
        # ``instance_ids`` falls back to ``resource_ids`` when no
        # machine_ids are known.  This is what the deprovisioning path
        # uses to scope a follow-up status read.
        assert result.data["instance_ids"] == ["deploy-orb-xyz"]

    def test_accepted_passes_through_rich_instances_from_handler_metadata(self) -> None:
        """Status-resolver-supplied instance dicts are propagated verbatim."""
        rich_instances = [
            {
                "instance_id": "orb-xyz-0000",
                "resource_id": "orb-xyz-0000",
                "instance_type": "k8s-pod",
                "image_id": "busybox:latest",
                "launch_time": "2026-01-01T00:00:00Z",
                "status": "pending",
            }
        ]
        outcome = Accepted(
            request_id="req-xyz",
            pending_resource_ids=["orb-xyz-0000"],
            metadata={
                "instances": rich_instances,
                "machine_ids": ["orb-xyz-0000"],
                "provider_api": "Pod",
            },
        )
        result = _outcome_to_provider_result(outcome, fallback_operation="get_instance_status")
        assert result.data["instances"] == rich_instances


# ---------------------------------------------------------------------------
# Completed
# ---------------------------------------------------------------------------


class TestCompletedOutcome:
    def test_completed_sets_fulfillment_final_true(self) -> None:
        outcome = Completed(
            resource_ids=["orb-done-0000"],
            metadata={"machine_ids": ["orb-done-0000"]},
        )
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert result.success is True
        # ``fulfillment_final=True`` tells the provisioning service the
        # request is in a terminal state — without it the request stays
        # in IN_PROGRESS forever.
        assert result.data["provider_data"]["fulfillment_final"] is True

    def test_completed_passes_through_rich_instances(self) -> None:
        rich_instances = [
            {
                "instance_id": "orb-done-0000",
                "resource_id": "orb-done-0000",
                "instance_type": "k8s-pod",
                "image_id": "busybox:latest",
                "launch_time": "2026-01-01T00:00:00Z",
                "status": "terminated",
            }
        ]
        outcome = Completed(
            resource_ids=["orb-done-0000"],
            metadata={"instances": rich_instances, "machine_ids": ["orb-done-0000"]},
        )
        result = _outcome_to_provider_result(outcome, fallback_operation="get_instance_status")
        assert result.data["instances"] == rich_instances


# ---------------------------------------------------------------------------
# RequiresFollowUp
# ---------------------------------------------------------------------------


class TestRequiresFollowUpOutcome:
    def test_termination_followup_populates_ids_from_context(self) -> None:
        """``RequiresFollowUp`` must hydrate ``resource_ids`` from the typed
        context so the application layer can keep tracking the pending
        resources instead of writing zero machine rows."""
        ctx = TerminationFollowUpContext(
            pending_instance_ids=["orb-a-0000", "orb-a-0001"],
            expected_terminal_state="terminated",
            provider_handle="follow-up-handle",
        )
        outcome = RequiresFollowUp(context=ctx, metadata={"provider_api": "Pod"})
        result = _outcome_to_provider_result(outcome, fallback_operation="terminate_instances")
        assert result.success is True
        assert result.data["resource_ids"] == ["orb-a-0000", "orb-a-0001"]
        assert result.data["instance_ids"] == ["orb-a-0000", "orb-a-0001"]
        assert result.data["provider_data"]["follow_up_kind"] == "termination"
        assert result.data["provider_data"]["provider_handle"] == "follow-up-handle"
        assert result.data["provider_data"]["expected_terminal_state"] == "terminated"

    def test_deployment_polling_followup_populates_ids_from_context(self) -> None:
        ctx = DeploymentPollingFollowUpContext(
            pending_resource_ids=["fleet-aaa"],
            expected_terminal_state="running",
            provider_handle="fleet-aaa",
        )
        outcome = RequiresFollowUp(context=ctx)
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert result.data["resource_ids"] == ["fleet-aaa"]
        assert result.data["instance_ids"] == ["fleet-aaa"]
        assert result.data["provider_data"]["follow_up_kind"] == "deployment_polling"
        assert result.data["provider_data"]["expected_terminal_state"] == "running"
