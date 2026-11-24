"""Exercise OCI volume identity, attachment and per-worker cleanup decisions."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.cli_client import OCICommandError
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler
from orb.providers.oci.services.secondary_volumes import OCISecondaryVolumes
from orb.providers.oci.volume_journal import OCIVolumeJournal, VolumeSlot

REQUEST = "req-one"
WORKER = "ocid1.instance.oc1.phx.worker"
VOLUME = "ocid1.volume.oc1.phx.disk"
ATTACHMENT = "ocid1.volumeattachment.oc1.phx.binding"


def saved_slot(index=0, **overrides):
    slot = VolumeSlot(
        index=index,
        spec={"size_in_gbs": 50, "attachment_type": "paravirtualized", "delete_on_return": True},
        compartment_id="compartment",
        availability_domain="AD",
        returning=True,
        create_started=True,
        attach_started=True,
        volume_id=VOLUME,
        attachment_id=ATTACHMENT,
        outcome=None,
    )
    return replace(slot, **overrides)


def volume(volume_id=VOLUME, state="AVAILABLE"):
    return {
        "id": volume_id,
        "availability-domain": "AD",
        "compartment-id": "compartment",
        "lifecycle-state": state,
    }


def binding(worker=WORKER, state="ATTACHED"):
    return {
        "id": ATTACHMENT,
        "instance-id": worker,
        "volume-id": VOLUME,
        "compartment-id": "compartment",
        "lifecycle-state": state,
    }


def service(slots, cli_results=(), post_results=()):
    journal = Mock(spec=OCIVolumeJournal)
    journal.slots.return_value = slots
    journal.retry_token.return_value = "stable"
    return OCISecondaryVolumes(
        journal, Mock(side_effect=cli_results), Mock(side_effect=post_results)
    )


@pytest.mark.parametrize("attachment_type", ["iscsi", "paravirtualized"])
def test_new_volume_uses_exact_create_and_attach_receipts(attachment_type):
    spec = {
        "size_in_gbs": 100,
        "vpus_per_gb": 20,
        "display_name": "data",
        "delete_on_return": True,
        "attachment_type": attachment_type,
    }
    slot = saved_slot(
        spec=spec,
        returning=False,
        create_started=False,
        attach_started=False,
        volume_id=None,
        attachment_id=None,
    )
    manager = service(
        [slot], [{"data": volume()}, {"data": binding()}], [{"data": volume()}, {"data": binding()}]
    )
    manager.journal.slots.side_effect = [[slot], [saved_slot(spec=spec)]]
    result = manager.reconcile(REQUEST, WORKER, release=False)
    assert result[0]["status"] == "attached" and result[0]["volume_id"] == VOLUME
    assert manager.post.call_args_list[0].args == (
        "volumes",
        {
            "availabilityDomain": "AD",
            "compartmentId": "compartment",
            "sizeInGBs": 100,
            "vpusPerGB": 20,
            "displayName": "data",
        },
        "stable",
    )
    assert manager.post.call_args_list[1].args == (
        "volumeAttachments",
        {"instanceId": WORKER, "volumeId": VOLUME, "type": attachment_type, "isShareable": False},
        "stable",
    )
    manager.journal.record_volume.assert_called_once_with(REQUEST, WORKER, 0, VOLUME)
    manager.journal.record_attachment.assert_called_once_with(REQUEST, WORKER, 0, ATTACHMENT)


def test_attached_volume_only_ready_after_verified_binding_read():
    manager = service(
        [saved_slot(returning=False)], [{"data": volume()}, {"data": binding(worker="foreign")}]
    )
    result = manager.reconcile(REQUEST, WORKER, release=False)
    assert result[0]["status"] == "unresolved"
    manager.post.assert_not_called()


@pytest.mark.parametrize("status,refused", [(403, True), (500, False), (429, False)])
def test_create_errors_never_fall_back_to_new_token(status, refused):
    manager = service(
        [saved_slot(returning=False, volume_id=None, attachment_id=None)],
        post_results=[OCICommandError("injected error", status=status)],
    )
    result = manager.reconcile(REQUEST, WORKER, release=False)
    assert result[0]["status"] == "unresolved"
    assert manager.post.call_count == 1
    assert manager.journal.refuse.called is refused
    manager.journal.record_volume.assert_not_called()


def test_return_preserves_unknown_create_and_handles_next_slot():
    manager = service(
        [
            saved_slot(volume_id=None, attachment_id=None),
            saved_slot(
                1, volume_id=None, attachment_id=None, create_started=False, attach_started=False
            ),
        ]
    )
    results = manager.reconcile(REQUEST, WORKER, release=True)
    assert [row["status"] for row in results] == ["unresolved", "skipped"]
    manager.post.assert_not_called()
    manager.cli.assert_not_called()
    manager.journal.finish.assert_called_once_with(REQUEST, WORKER, 1, "skipped")


def test_return_waits_for_oci_detach_and_holds_foreign_volume():
    for attachments, expected in [
        ([binding()], "pending"),
        ([binding(worker="foreign")], "unresolved"),
    ]:
        manager = service(
            [saved_slot()], [{"data": volume(), "etag": "first"}, {"data": attachments}]
        )
        result = manager.reconcile(REQUEST, WORKER, release=True)
        assert result[0]["status"] == expected
        assert manager.cli.call_args.args[0][-1] == "--all"
        assert manager.cli.call_count == 2
        manager.journal.finish.assert_not_called()


def test_delete_is_conditional_and_requires_terminal_readback():
    manager = service(
        [saved_slot()],
        [
            {"data": volume(), "etag": "version"},
            {"data": []},
            {},
            {"data": volume(state="TERMINATED")},
        ],
    )
    assert manager.reconcile(REQUEST, WORKER, release=True)[0]["status"] == "pending"
    assert manager.cli.call_args.args[0] == [
        "bv",
        "volume",
        "delete",
        "--volume-id",
        VOLUME,
        "--if-match",
        "version",
        "--force",
        "--no-retry",
    ]
    assert manager.reconcile(REQUEST, WORKER, release=True)[0]["status"] == "deleted"
    manager.journal.finish.assert_called_once_with(REQUEST, WORKER, 0, "deleted")


@pytest.mark.parametrize(
    "bad_response",
    [
        {"data": volume("ocid1.volume.oc1.phx.foreign"), "etag": "x"},
        OCICommandError("not found or not authorized", status=404),
    ],
)
def test_unknown_volume_is_never_accepted_as_deleted(bad_response):
    manager = service([saved_slot()], [bad_response])
    assert manager.reconcile(REQUEST, WORKER, release=True)[0]["status"] == "unresolved"
    assert manager.cli.call_count == 1
    manager.journal.finish.assert_not_called()


def test_return_reports_retained_id_and_never_deletes_it():
    slot = saved_slot(spec={"size_in_gbs": 50, "delete_on_return": False})
    manager = service(
        [slot], [{"data": volume(), "etag": "version"}, {"data": [binding(state="DETACHED")]}]
    )
    assert manager.reconcile(REQUEST, WORKER, release=True)[0] == {
        "slot": 0,
        "delete_on_return": False,
        "status": "retained",
        "volume_id": VOLUME,
        "attachment_id": ATTACHMENT,
    }
    assert manager.cli.call_count == 2


def test_return_fence_prevents_acquisition_after_cancel():
    journal = Mock(spec=OCIVolumeJournal)
    journal.slots.return_value = [saved_slot(returning=False, volume_id=None, attachment_id=None)]
    journal.abandoned.return_value = True
    handler = OCIComputeHandler(Mock(), "us-phoenix-1", volume_journal=journal)
    handler._run_oci = Mock(return_value={})
    assert handler._volume_status(REQUEST, WORKER, "RUNNING")[0] == "shutting-down"
    journal.start_return.assert_called_once_with(REQUEST, WORKER)
    journal.retry_token.assert_not_called()
    handler._run_oci.assert_called_once_with(
        ["compute", "instance", "terminate", "--instance-id", WORKER, "--force"]
    )


def test_ambiguous_worker_launch_with_disks_uses_signed_replay_only():
    handler = OCIComputeHandler(Mock(), "us-phoenix-1", volume_journal=Mock(spec=OCIVolumeJournal))
    payload = {
        "secondary_volumes": [{"size_in_gbs": 50}],
        "retry_token": "stable",
        "freeform_tags": {"orb_request_id": REQUEST},
    }
    handler._launch_with_cli = Mock(
        side_effect=[OCICommandError("timeout", reason="timeout"), {"data": {"id": WORKER}}]
    )
    handler._reconcile_launch = Mock(
        side_effect=AssertionError("mutable worker tags are insufficient")
    )
    assert handler._launch_idempotently(payload) == {"data": {"id": WORKER}}
    assert handler._launch_with_cli.call_count == 2
    assert handler._launch_with_cli.call_args_list[0] == handler._launch_with_cli.call_args_list[1]


@pytest.mark.parametrize("fallback", [False, True])
def test_initial_running_worker_pending_until_secondary_volumes_ready(fallback):
    journal = Mock(spec=OCIVolumeJournal)
    handler = OCIComputeHandler(Mock(), "us-phoenix-1", run_command=Mock(), volume_journal=journal)
    running = {"data": {"id": WORKER, "lifecycle-state": "RUNNING", "availability-domain": "AD"}}
    handler._launch_idempotently = Mock(
        side_effect=[OCICommandError("no preemptible capacity", code="OutOfHostCapacity"), running]
        if fallback
        else [running]
    )
    op = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={
            "request_id": REQUEST,
            "template_id": "tpl",
            "count": 1,
            "image_id": "ocid1.image.oc1..i",
            "instance_type": "VM.Standard.E5.Flex",
            "subnet_ids": ["ocid1.subnet.oc1..s"],
            "compartment_id": "compartment",
            "availability_domain": "AD",
            "capacity_type": "preemptible" if fallback else "ondemand",
            "fallback_to_ondemand": fallback,
            "secondary_volumes": [{"size_in_gbs": 50, "delete_on_return": True}],
        },
    )
    result = handler._create_instances(op)
    assert result.success, result.error_message
    assert result.data["instances"][0]["status"] == "pending"
    assert result.data["fallback_attempted"] is fallback
    journal.begin.assert_called_once()
    journal.retry_token.assert_not_called()


def test_missing_fence_and_incomplete_inventory_cannot_authorize_delete():
    for slot, reads in [
        (saved_slot(returning=False), []),
        (saved_slot(), [{"data": volume(), "etag": "e"}, {"data": [], "opc-next-page": "more"}]),
    ]:
        manager = service([slot], reads)
        assert manager.reconcile(REQUEST, WORKER, release=True)[0]["status"] == "unresolved"
        assert manager.cli.call_count == len(reads)
        manager.journal.finish.assert_not_called()


def test_unresolved_volume_visible_in_status_verdict():
    journal = Mock(spec=OCIVolumeJournal)
    journal.origin_for_worker.return_value = REQUEST
    journal.worker_details.return_value = {
        "instance_type": "VM.Standard.E5.Flex",
        "image_id": "ocid1.image.oc1..saved",
    }
    journal.slots.return_value = [saved_slot()]
    journal.abandoned.return_value = False
    handler = OCIComputeHandler(Mock(), "us-phoenix-1", run_command=Mock(), volume_journal=journal)
    handler._run_oci = Mock(
        side_effect=[
            {
                "data": {
                    "id": WORKER,
                    "lifecycle-state": "TERMINATED",
                    "freeform-tags": {"orb_secondary_volume_request": REQUEST},
                }
            },
            OCICommandError("inaccessible volume", status=404),
        ]
    )
    op = ProviderOperation(
        operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
        parameters={"machine_ids": [WORKER]},
    )
    result = handler.get_instance_status(op)
    assert result.success
    assert "inaccessible volume" in result.metadata["provider_fulfilment"].message
    assert result.data["instances"][0]["status"] == "shutting-down"
    assert result.data["instances"][0]["image_id"] == "ocid1.image.oc1..saved"
    assert result.metadata["secondary_volumes"][WORKER][0]["status"] == "unresolved"


def test_stopped_worker_cannot_complete_return_before_volume_cleanup():
    journal = Mock(spec=OCIVolumeJournal)
    journal.origin_for_worker.return_value = REQUEST
    journal.worker_details.return_value = {
        "instance_type": "VM.Standard.E5.Flex",
        "image_id": "ocid1.image.oc1..saved",
    }
    journal.slots.return_value = [saved_slot()]
    handler = OCIComputeHandler(Mock(), "us-phoenix-1", volume_journal=journal)
    handler._run_oci = Mock(return_value={})
    data = {"id": WORKER, "freeform-tags": {"orb_secondary_volume_request": REQUEST}}
    status, _ = handler._volume_poll({}, WORKER, "STOPPED", "stopped", True, data)
    assert status == "shutting-down"
    handler._run_oci.assert_called_once_with(
        ["compute", "instance", "terminate", "--instance-id", WORKER, "--force"]
    )
    journal.finish.assert_not_called()


@pytest.mark.parametrize(
    ("begin_result", "fence_error", "terminated"),
    [
        (False, None, False),
        (ValueError("conflicting saved plan"), None, False),
        (True, ValueError("worker already accepted"), False),
        (True, None, True),
    ],
)
def test_batch_rollback_only_terminates_new_and_unaccepted_workers(
    begin_result, fence_error, terminated
):
    journal = Mock(spec=OCIVolumeJournal)
    journal.begin.side_effect = [begin_result]
    journal.start_return.side_effect = fence_error
    handler = OCIComputeHandler(Mock(), "us-phoenix-1", run_command=Mock(), volume_journal=journal)
    handler._launch_idempotently = Mock(
        side_effect=[
            {"data": {"id": WORKER, "availability-domain": "AD"}},
            RuntimeError("second worker could not launch"),
        ]
    )
    handler._run_oci = Mock()
    op = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={
            "request_id": REQUEST,
            "template_id": "tpl",
            "count": 2,
            "image_id": "ocid1.image.oc1..image",
            "instance_type": "VM.Standard.E5.Flex",
            "subnet_ids": ["ocid1.subnet.oc1..subnet"],
            "compartment_id": "compartment",
            "availability_domain": "AD",
            "secondary_volumes": [{"size_in_gbs": 50, "delete_on_return": True}],
        },
    )
    result = handler._create_instances(op)
    assert not result.success
    assert result.metadata["rollback_requested_instance_ids"] == ([WORKER] if terminated else [])
    if terminated:
        assert result.metadata["rollback_errors"] == []
        assert handler._run_oci.call_args.args[0][:3] == ["compute", "instance", "terminate"]
        journal.start_return.assert_called_once_with(REQUEST, WORKER, unaccepted_only=True)
    else:
        assert result.metadata["rollback_errors"][0]["instance_id"] == WORKER
        handler._run_oci.assert_not_called()


def test_arbitrary_worker_tag_does_not_opt_an_ordinary_template_into_volume_management():
    journal = Mock(spec=OCIVolumeJournal)
    handler = OCIComputeHandler(Mock(), "us-phoenix-1", volume_journal=journal)
    params = {"machine_coordinates": {WORKER: {"request_id": REQUEST, "provider_data": {}}}}
    worker = {"id": WORKER, "freeform-tags": {"orb_secondary_volume_request": "unrelated-request"}}
    assert handler._volume_poll(params, WORKER, "RUNNING", "running", False, worker) == (
        "running",
        {},
    )
    journal.slots.assert_not_called()
    journal.origin_for_worker.assert_not_called()


def test_registered_worker_with_unreadable_volume_plan_rejects_readiness():
    journal = Mock(spec=OCIVolumeJournal)
    journal.slots.side_effect = RuntimeError("journal unavailable")
    handler = OCIComputeHandler(Mock(), "us-phoenix-1", volume_journal=journal)
    params = {
        "machine_coordinates": {
            WORKER: {
                "request_id": REQUEST,
                "provider_data": {OCIVolumeJournal.plan_key(WORKER): {"region": "us-phoenix-1"}},
            }
        }
    }
    status, data = handler._volume_poll(params, WORKER, "RUNNING", "running", False, {"id": WORKER})
    assert status == "pending" and data["secondary_volumes"][0]["status"] == "unresolved"
