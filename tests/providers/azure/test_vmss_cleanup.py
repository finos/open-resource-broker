from unittest.mock import MagicMock

from orb.providers.azure.infrastructure.vmss_cleanup import (
    PendingVmssCleanup,
    VmssCleanupCoordinator,
)


def test_pending_vmss_cleanup_round_trips_metadata():
    cleanup = PendingVmssCleanup.create(
        resource_group="test-rg",
        vmss_name="vmss-demo",
        machine_ids=["vm-a", "vm-a", "vm-b"],
        delete_vmss_when_empty=True,
        member_delete_submitted=True,
        delete_retry_pending=True,
        last_delete_error="transient delete failure",
    )

    restored = PendingVmssCleanup.from_metadata(cleanup.to_metadata())

    assert restored is not None
    assert restored.to_metadata() == {
        "resource_group": "test-rg",
        "vmss_name": "vmss-demo",
        "machine_ids": ["vm-a", "vm-b"],
        "delete_vmss_when_empty": True,
        "member_delete_submitted": True,
        "delete_submission_semantics": "best_effort_without_reverification",
        "delete_submitted": False,
        "delete_retry_pending": True,
        "last_delete_error": "transient delete failure",
    }


def test_pending_vmss_cleanup_merge_preserves_ids_and_retry_state():
    base = PendingVmssCleanup.from_metadata(
        {
            "resource_group": "test-rg",
            "vmss_name": "vmss-demo",
            "machine_ids": ["vm-a"],
            "delete_vmss_when_empty": True,
            "member_delete_submitted": True,
        }
    )
    update = PendingVmssCleanup.from_metadata(
        {
            "resource_group": "test-rg",
            "vmss_name": "vmss-demo",
            "machine_ids": ["vm-b"],
            "delete_vmss_when_empty": True,
            "delete_retry_pending": True,
            "last_delete_error": "still deleting",
        }
    )

    assert base is not None
    assert update is not None

    merged = base.combine_for_same_vmss(update)

    assert merged.to_status_detail() == {
        "resource_group": "test-rg",
        "vmss_name": "vmss-demo",
        "machine_ids": ["vm-a", "vm-b"],
        "delete_vmss_when_empty": True,
        "member_delete_submitted": True,
        "delete_submission_semantics": "best_effort_without_reverification",
        "delete_submitted": False,
        "delete_retry_pending": True,
        "last_delete_error": "still deleting",
    }


def test_vmss_cleanup_coordinator_reconciles_delete_retry_state():
    logger = MagicMock()
    coordinator = VmssCleanupCoordinator(
        logger=logger,
        get_vmss_member_count=lambda **_: 0,
        vmss_exists=lambda **_: True,
        begin_delete_vmss=lambda **_: (_ for _ in ()).throw(RuntimeError("delete blocked")),
    )

    coordinator.record(
        {
            "provider_data": {
                "pending_resource_cleanup": {
                    "resource_group": "test-rg",
                    "vmss_name": "vmss-demo",
                    "machine_ids": ["vm-a"],
                    "delete_vmss_when_empty": True,
                }
            }
        }
    )

    coordinator.reconcile(
        resource_group="test-rg",
        resource_ids=["vmss-demo"],
        observed_ids=set(),
    )

    assert coordinator.status_metadata(
        resource_group="test-rg",
        resource_ids=["vmss-demo"],
    ) == {
        "termination_follow_up_pending": True,
        "termination_follow_up_details": [
            {
                "resource_group": "test-rg",
                "vmss_name": "vmss-demo",
                "machine_ids": ["vm-a"],
                "delete_vmss_when_empty": True,
                "member_delete_submitted": True,
                "delete_submission_semantics": "best_effort_without_reverification",
                "delete_submitted": False,
                "delete_retry_pending": True,
                "last_delete_error": "delete blocked",
            }
        ],
    }
    logger.warning.assert_called_once()


def test_vmss_cleanup_coordinator_restores_pending_state_from_request_metadata():
    logger = MagicMock()
    coordinator = VmssCleanupCoordinator(
        logger=logger,
        get_vmss_member_count=lambda **_: 1,
        vmss_exists=lambda **_: True,
        begin_delete_vmss=lambda **_: None,
    )

    coordinator.restore_from_request_metadata(
        {
            "termination_requests": [
                {
                    "pending_resource_cleanup": {
                        "resource_group": "test-rg",
                        "vmss_name": "vmss-demo",
                        "machine_ids": ["vm-a"],
                        "delete_vmss_when_empty": True,
                    }
                }
            ]
        }
    )

    assert coordinator.has_pending(resource_group="test-rg", resource_ids=["vmss-demo"]) is True
    assert coordinator.status_metadata(
        resource_group="test-rg",
        resource_ids=["vmss-demo"],
    )["termination_follow_up_details"] == [
        {
            "resource_group": "test-rg",
            "vmss_name": "vmss-demo",
            "machine_ids": ["vm-a"],
            "delete_vmss_when_empty": True,
            "member_delete_submitted": True,
            "delete_submission_semantics": "best_effort_without_reverification",
            "delete_submitted": False,
            "delete_retry_pending": False,
        }
    ]


def test_vmss_cleanup_coordinator_clears_pending_state():
    coordinator = VmssCleanupCoordinator(
        logger=MagicMock(),
        get_vmss_member_count=lambda **_: 1,
        vmss_exists=lambda **_: True,
        begin_delete_vmss=lambda **_: None,
    )
    coordinator.record(
        {
            "provider_data": {
                "pending_resource_cleanup": {
                    "resource_group": "test-rg",
                    "vmss_name": "vmss-demo",
                    "machine_ids": ["vm-a"],
                    "delete_vmss_when_empty": True,
                }
            }
        }
    )

    coordinator.clear()

    assert coordinator.has_pending(resource_group="test-rg", resource_ids=["vmss-demo"]) is False


def test_vmss_cleanup_coordinator_submits_delete_when_vmss_is_empty():
    begin_delete_vmss = MagicMock()
    coordinator = VmssCleanupCoordinator(
        logger=MagicMock(),
        get_vmss_member_count=lambda **_: 0,
        vmss_exists=lambda **_: True,
        begin_delete_vmss=begin_delete_vmss,
    )
    coordinator.record(
        {
            "provider_data": {
                "pending_resource_cleanup": {
                    "resource_group": "test-rg",
                    "vmss_name": "vmss-demo",
                    "machine_ids": ["vm-a"],
                    "delete_vmss_when_empty": True,
                }
            }
        }
    )

    coordinator.reconcile(
        resource_group="test-rg",
        resource_ids=["vmss-demo"],
        observed_ids=set(),
    )

    begin_delete_vmss.assert_called_once_with(resource_group="test-rg", vmss_name="vmss-demo")
    assert coordinator.status_metadata(
        resource_group="test-rg",
        resource_ids=["vmss-demo"],
    ) == {
        "termination_follow_up_pending": True,
        "termination_follow_up_details": [
            {
                "resource_group": "test-rg",
                "vmss_name": "vmss-demo",
                "machine_ids": ["vm-a"],
                "delete_vmss_when_empty": True,
                "member_delete_submitted": True,
                "delete_submission_semantics": "best_effort_without_reverification",
                "delete_submitted": True,
                "delete_retry_pending": False,
            }
        ],
    }


def test_pending_vmss_cleanup_defaults_member_delete_submission_for_legacy_metadata():
    restored = PendingVmssCleanup.from_metadata(
        {
            "resource_group": "test-rg",
            "vmss_name": "vmss-demo",
            "machine_ids": ["vm-a"],
            "delete_vmss_when_empty": True,
            "delete_retry_pending": True,
        }
    )

    assert restored is not None
    assert restored.member_delete_submitted is True
