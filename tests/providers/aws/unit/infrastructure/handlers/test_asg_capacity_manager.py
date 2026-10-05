"""Unit tests for ASGCapacityManager.

Covers pre-termination capacity reduction (reduce_capacity), weight-aware
instance release (release_instances), the ASG-membership idempotency guard
(_filter_asg_members), and the ASG-deletion callback fallback.
"""

from unittest.mock import MagicMock

from orb.providers.aws.infrastructure.handlers.asg.capacity_manager import ASGCapacityManager


def _identity_retry(fn, operation_type="standard", **kwargs):  # noqa: ARG001
    return fn(**kwargs)


def _chunk_list(items, size):
    return [items[i : i + size] for i in range(0, len(items), size)]


def _make_manager(
    aws_client=None,
    aws_ops=None,
    request_adapter=None,
    cleanup_fn=None,
    logger=None,
    retry_with_backoff=None,
    chunk_list=None,
) -> tuple[ASGCapacityManager, MagicMock, MagicMock, MagicMock]:
    aws_client = aws_client or MagicMock()
    aws_ops = aws_ops or MagicMock()
    logger = logger or MagicMock()
    cleanup_fn = cleanup_fn or MagicMock()
    manager = ASGCapacityManager(
        aws_client=aws_client,
        aws_ops=aws_ops,
        request_adapter=request_adapter,
        cleanup_on_zero_capacity_fn=cleanup_fn,
        logger=logger,
        retry_with_backoff=retry_with_backoff or _identity_retry,
        chunk_list=chunk_list or _chunk_list,
    )
    return manager, aws_client, aws_ops, cleanup_fn


# ---------------------------------------------------------------------------
# reduce_capacity()
# ---------------------------------------------------------------------------


def test_reduce_capacity_empty_instance_ids_is_noop():
    manager, aws_client, _, _ = _make_manager()
    manager.reduce_capacity([])
    aws_client.autoscaling_client.describe_auto_scaling_instances.assert_not_called()


def test_reduce_capacity_describe_instances_failure_logs_warning_and_returns():
    manager, aws_client, _, _ = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.side_effect = RuntimeError(
        "throttled"
    )
    manager.reduce_capacity(["i-1"])
    aws_client.autoscaling_client.update_auto_scaling_group.assert_not_called()


def test_reduce_capacity_no_matching_groups_is_noop():
    manager, aws_client, _, _ = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": []
    }
    manager.reduce_capacity(["i-1"])
    aws_client.autoscaling_client.describe_auto_scaling_groups.assert_not_called()


def test_reduce_capacity_updates_desired_and_min_size():
    manager, aws_client, _, _ = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [
            {"AutoScalingGroupName": "asg-1", "InstanceId": "i-1"},
            {"AutoScalingGroupName": "asg-1", "InstanceId": "i-2"},
        ]
    }
    aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": [{"DesiredCapacity": 5, "MinSize": 3}]
    }
    manager.reduce_capacity(["i-1", "i-2"])

    aws_client.autoscaling_client.update_auto_scaling_group.assert_called_once_with(
        AutoScalingGroupName="asg-1", DesiredCapacity=3, MinSize=3
    )


def test_reduce_capacity_clamps_min_size_to_new_desired():
    manager, aws_client, _, _ = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [{"AutoScalingGroupName": "asg-1", "InstanceId": "i-1"}]
    }
    aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": [{"DesiredCapacity": 1, "MinSize": 1}]
    }
    manager.reduce_capacity(["i-1"])

    # new_desired = max(0, 1-1) = 0; new_min = min(1, 0) = 0
    aws_client.autoscaling_client.update_auto_scaling_group.assert_called_once_with(
        AutoScalingGroupName="asg-1", DesiredCapacity=0, MinSize=0
    )


def test_reduce_capacity_skips_update_when_no_change_needed():
    manager, aws_client, _, _ = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [{"AutoScalingGroupName": "asg-1", "InstanceId": "i-1"}]
    }
    # Instance count (1) removal would not change desired/min since both already 0.
    aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": [{"DesiredCapacity": 0, "MinSize": 0}]
    }
    manager.reduce_capacity(["i-1"])
    aws_client.autoscaling_client.update_auto_scaling_group.assert_not_called()


def test_reduce_capacity_skips_group_with_no_describe_results():
    manager, aws_client, _, _ = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [{"AutoScalingGroupName": "asg-1", "InstanceId": "i-1"}]
    }
    aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": []
    }
    manager.reduce_capacity(["i-1"])
    aws_client.autoscaling_client.update_auto_scaling_group.assert_not_called()


def test_reduce_capacity_continues_after_describe_group_failure():
    manager, aws_client, _, _ = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [
            {"AutoScalingGroupName": "asg-1", "InstanceId": "i-1"},
            {"AutoScalingGroupName": "asg-2", "InstanceId": "i-2"},
        ]
    }

    def _describe_groups(AutoScalingGroupNames, **_kw):  # noqa: N803
        if AutoScalingGroupNames == ["asg-1"]:
            raise RuntimeError("boom")
        return {"AutoScalingGroups": [{"DesiredCapacity": 2, "MinSize": 1}]}

    aws_client.autoscaling_client.describe_auto_scaling_groups.side_effect = _describe_groups
    manager.reduce_capacity(["i-1", "i-2"])

    aws_client.autoscaling_client.update_auto_scaling_group.assert_called_once_with(
        AutoScalingGroupName="asg-2", DesiredCapacity=1, MinSize=1
    )


def test_reduce_capacity_logs_warning_when_update_fails():
    manager, aws_client, _, _ = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [{"AutoScalingGroupName": "asg-1", "InstanceId": "i-1"}]
    }
    aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": [{"DesiredCapacity": 5, "MinSize": 3}]
    }
    aws_client.autoscaling_client.update_auto_scaling_group.side_effect = RuntimeError("boom")
    manager.reduce_capacity(["i-1"])  # must not raise


# ---------------------------------------------------------------------------
# _filter_asg_members()
# ---------------------------------------------------------------------------


def test_filter_asg_members_empty_input_returns_empty():
    manager, _, _, _ = _make_manager()
    assert manager._filter_asg_members("asg-1", []) == []


def test_filter_asg_members_no_entries_falls_back_to_all():
    manager, aws_client, _, _ = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": []
    }
    assert manager._filter_asg_members("asg-1", ["i-1", "i-2"]) == ["i-1", "i-2"]


def test_filter_asg_members_none_matching_asg_falls_back_to_all():
    manager, aws_client, _, _ = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [
            {
                "InstanceId": "i-1",
                "AutoScalingGroupName": "other-asg",
                "LifecycleState": "InService",
            }
        ]
    }
    assert manager._filter_asg_members("asg-1", ["i-1"]) == ["i-1"]


def test_filter_asg_members_keeps_only_detachable_states():
    manager, aws_client, _, _ = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [
            {"InstanceId": "i-1", "AutoScalingGroupName": "asg-1", "LifecycleState": "InService"},
            {"InstanceId": "i-2", "AutoScalingGroupName": "asg-1", "LifecycleState": "Standby"},
            {
                "InstanceId": "i-3",
                "AutoScalingGroupName": "asg-1",
                "LifecycleState": "Terminating",
            },
        ]
    }
    result = manager._filter_asg_members("asg-1", ["i-1", "i-2", "i-3"])
    assert result == ["i-1", "i-2"]


def test_filter_asg_members_exception_falls_back_to_all():
    manager, aws_client, _, _ = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.side_effect = RuntimeError("boom")
    assert manager._filter_asg_members("asg-1", ["i-1"]) == ["i-1"]


# ---------------------------------------------------------------------------
# release_instances() — missing asg_details path
# ---------------------------------------------------------------------------


def test_release_instances_recovers_asg_details_via_describe():
    manager, aws_client, aws_ops, _cleanup_fn = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": [{"DesiredCapacity": 1, "MinSize": 1}]
    }
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [
            {"InstanceId": "i-1", "AutoScalingGroupName": "asg-1", "LifecycleState": "InService"}
        ]
    }
    manager.release_instances("asg-1", ["i-1"], {})

    aws_client.autoscaling_client.terminate_instance_in_auto_scaling_group.assert_called_once()
    aws_ops.terminate_instances_with_fallback.assert_not_called()


def test_release_instances_direct_termination_when_asg_details_unrecoverable():
    manager, aws_client, aws_ops, cleanup_fn = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_groups.side_effect = RuntimeError(
        "describe failed"
    )
    manager.release_instances("asg-1", ["i-1"], {})

    aws_ops.terminate_instances_with_fallback.assert_called_once_with(
        ["i-1"], None, "ASG asg-1 instances (no ASG details)"
    )
    aws_client.autoscaling_client.delete_auto_scaling_group.assert_called_once_with(
        AutoScalingGroupName="asg-1", ForceDelete=True
    )
    cleanup_fn.assert_called_once_with("asg", "asg-1")


def test_release_instances_direct_termination_when_describe_returns_no_groups():
    manager, aws_client, aws_ops, cleanup_fn = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": []
    }
    manager.release_instances("asg-1", ["i-1"], {})

    aws_ops.terminate_instances_with_fallback.assert_called_once()
    cleanup_fn.assert_called_once_with("asg", "asg-1")


def test_release_instances_uses_registered_delete_asg_callback():
    manager, aws_client, _aws_ops, _cleanup_fn = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": []
    }
    delete_fn = MagicMock()
    manager.set_delete_asg_fn(delete_fn)

    manager.release_instances("asg-1", ["i-1"], {})

    delete_fn.assert_called_once_with("asg-1")
    aws_client.autoscaling_client.delete_auto_scaling_group.assert_not_called()


# ---------------------------------------------------------------------------
# release_instances() — happy path with asg_details provided
# ---------------------------------------------------------------------------


def test_release_instances_all_members_already_gone_terminates_directly():
    manager, aws_client, aws_ops, _cleanup_fn = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [
            {
                "InstanceId": "i-1",
                "AutoScalingGroupName": "asg-1",
                "LifecycleState": "Terminated",
            }
        ]
    }
    manager.release_instances("asg-1", ["i-1"], {"DesiredCapacity": 2, "MinSize": 1})

    aws_ops.terminate_instances_with_fallback.assert_called_once_with(
        ["i-1"], None, "ASG asg-1 instances (already detached)"
    )
    aws_client.autoscaling_client.terminate_instance_in_auto_scaling_group.assert_not_called()


def test_release_instances_logs_skipped_subset_and_terminates_rest():
    manager, aws_client, _aws_ops, _cleanup_fn = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [
            {"InstanceId": "i-1", "AutoScalingGroupName": "asg-1", "LifecycleState": "InService"},
            {
                "InstanceId": "i-2",
                "AutoScalingGroupName": "asg-1",
                "LifecycleState": "Terminated",
            },
        ]
    }
    aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": [{"DesiredCapacity": 1}]
    }
    manager.release_instances("asg-1", ["i-1", "i-2"], {"DesiredCapacity": 2, "MinSize": 1})

    aws_client.autoscaling_client.terminate_instance_in_auto_scaling_group.assert_called_once_with(
        InstanceId="i-1", ShouldDecrementDesiredCapacity=True
    )


def test_release_instances_falls_back_to_computed_capacity_when_redescribe_fails():
    manager, aws_client, _aws_ops, cleanup_fn = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [
            {"InstanceId": "i-1", "AutoScalingGroupName": "asg-1", "LifecycleState": "InService"}
        ]
    }
    aws_client.autoscaling_client.describe_auto_scaling_groups.side_effect = RuntimeError("boom")

    manager.release_instances("asg-1", ["i-1"], {"DesiredCapacity": 1, "MinSize": 1})

    # live_desired falls back to max(0, 1 - 1) == 0 -> new_capacity == 0 -> delete ASG
    aws_client.autoscaling_client.delete_auto_scaling_group.assert_called_once_with(
        AutoScalingGroupName="asg-1", ForceDelete=True
    )
    cleanup_fn.assert_called_once_with("asg", "asg-1")


def test_release_instances_reduces_min_size_when_above_new_capacity():
    manager, aws_client, _aws_ops, cleanup_fn = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [
            {"InstanceId": "i-1", "AutoScalingGroupName": "asg-1", "LifecycleState": "InService"}
        ]
    }
    aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": [{"DesiredCapacity": 3}]
    }
    manager.release_instances("asg-1", ["i-1"], {"DesiredCapacity": 4, "MinSize": 4})

    aws_client.autoscaling_client.update_auto_scaling_group.assert_called_once_with(
        AutoScalingGroupName="asg-1", MinSize=3
    )
    # Non-zero capacity -> ASG not deleted.
    aws_client.autoscaling_client.delete_auto_scaling_group.assert_not_called()
    cleanup_fn.assert_not_called()


def test_release_instances_no_min_size_update_when_already_at_or_below_capacity():
    manager, aws_client, _aws_ops, _cleanup_fn = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_instances.return_value = {
        "AutoScalingInstances": [
            {"InstanceId": "i-1", "AutoScalingGroupName": "asg-1", "LifecycleState": "InService"}
        ]
    }
    aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": [{"DesiredCapacity": 3}]
    }
    manager.release_instances("asg-1", ["i-1"], {"DesiredCapacity": 4, "MinSize": 2})

    aws_client.autoscaling_client.update_auto_scaling_group.assert_not_called()


# ---------------------------------------------------------------------------
# _delete_asg_direct() failure path
# ---------------------------------------------------------------------------


def test_delete_asg_direct_logs_warning_on_failure():
    manager, aws_client, _aws_ops, _cleanup_fn = _make_manager()
    aws_client.autoscaling_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": []
    }
    aws_client.autoscaling_client.delete_auto_scaling_group.side_effect = RuntimeError("boom")

    manager.release_instances("asg-1", ["i-1"], {})  # must not raise

    aws_client.autoscaling_client.delete_auto_scaling_group.assert_called_once()
