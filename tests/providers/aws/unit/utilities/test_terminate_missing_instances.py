"""Unit tests for terminating instances that EC2 may no longer know."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from orb.infrastructure.resilience.retry_decorator import retry
from orb.providers.aws.utilities.aws_operations import AWSOperations
from orb.providers.aws.utilities.terminate_instances import terminate_skipping_missing


def _error(code: str, message: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "TerminateInstances")


def _not_found(*ids: str) -> ClientError:
    quoted = ", ".join(ids)
    if len(ids) == 1:
        message = f"The instance ID '{quoted}' does not exist"
    else:
        message = f"The instance IDs '{quoted}' do not exist"
    return _error("InvalidInstanceID.NotFound", message)


class FakeEC2:
    """EC2 stand-in with the all-or-nothing TerminateInstances behaviour."""

    def __init__(self, existing: set[str], message_names_ids: bool = True) -> None:
        self.existing = set(existing)
        self.message_names_ids = message_names_ids
        self.calls: list[list[str]] = []

    def terminate_instances(self, InstanceIds: list[str]) -> dict[str, Any]:
        self.calls.append(list(InstanceIds))
        unknown = [i for i in InstanceIds if i not in self.existing]
        if unknown:
            if self.message_names_ids:
                raise _not_found(*unknown)
            raise _error("InvalidInstanceID.NotFound", "The instance does not exist")
        return {
            "TerminatingInstances": [
                {
                    "InstanceId": i,
                    "PreviousState": {"Name": "running"},
                    "CurrentState": {"Name": "shutting-down"},
                }
                for i in InstanceIds
            ]
        }


def _ops(ec2: FakeEC2) -> AWSOperations:
    client = MagicMock()
    client.ec2_client = ec2
    ops = AWSOperations(aws_client=client, logger=MagicMock())

    def retry_with_backoff(func: Any, *args: Any, operation_type: str = "standard", **kw: Any):
        @retry(strategy="exponential", max_attempts=3, base_delay=0.0, jitter=False)
        def wrapped() -> Any:
            return func(*args, **kw)

        return wrapped()

    ops.set_retry_method(retry_with_backoff)
    return ops


class TestTerminateInstancesWithFallback:
    def test_not_found_is_not_retried(self):
        ec2 = FakeEC2(existing=set())
        result = _ops(ec2).terminate_instances_with_fallback(["i-gone"])

        assert len(ec2.calls) == 1
        assert result["already_terminated"] == ["i-gone"]
        assert result["TerminatingInstances"] == []

    def test_batch_with_one_unknown_id_terminates_the_others(self):
        ec2 = FakeEC2(existing={"i-a", "i-b"})
        result = _ops(ec2).terminate_instances_with_fallback(["i-a", "i-gone", "i-b"])

        assert [i["InstanceId"] for i in result["TerminatingInstances"]] == ["i-a", "i-b"]
        assert result["already_terminated"] == ["i-gone"]
        assert ec2.existing == {"i-a", "i-b"}
        assert ec2.calls == [["i-a", "i-gone", "i-b"], ["i-a", "i-b"]]

    def test_other_errors_still_raise(self):
        ec2 = MagicMock()
        ec2.terminate_instances.side_effect = _error("UnauthorizedOperation", "denied")
        with pytest.raises(Exception, match="denied"):
            _ops(ec2).terminate_instances_with_fallback(["i-a"])


class TestTerminateSkippingMissing:
    def test_all_present(self):
        ec2 = FakeEC2(existing={"i-a", "i-b"})
        result = terminate_skipping_missing(
            lambda ids: ec2.terminate_instances(InstanceIds=ids), ["i-a", "i-b"]
        )
        assert result["already_terminated"] == []
        assert len(result["TerminatingInstances"]) == 2
        assert len(ec2.calls) == 1

    def test_several_unknown_ids_in_one_message(self):
        ec2 = FakeEC2(existing={"i-a"})
        result = terminate_skipping_missing(
            lambda ids: ec2.terminate_instances(InstanceIds=ids), ["i-x", "i-a", "i-y"]
        )
        assert result["already_terminated"] == ["i-x", "i-y"]
        assert [i["InstanceId"] for i in result["TerminatingInstances"]] == ["i-a"]

    def test_message_without_ids_falls_back_to_one_at_a_time(self):
        ec2 = FakeEC2(existing={"i-a", "i-b"}, message_names_ids=False)
        result = terminate_skipping_missing(
            lambda ids: ec2.terminate_instances(InstanceIds=ids), ["i-a", "i-gone", "i-b"]
        )
        assert result["already_terminated"] == ["i-gone"]
        assert [i["InstanceId"] for i in result["TerminatingInstances"]] == ["i-a", "i-b"]

    def test_duplicate_ids_are_sent_once(self):
        ec2 = FakeEC2(existing={"i-a"})
        terminate_skipping_missing(
            lambda ids: ec2.terminate_instances(InstanceIds=ids), ["i-a", "i-a"]
        )
        assert ec2.calls == [["i-a"]]

    def test_other_client_error_propagates(self):
        def terminate(ids: list[str]) -> dict[str, Any]:
            raise _error("RequestLimitExceeded", "slow down")

        with pytest.raises(ClientError):
            terminate_skipping_missing(terminate, ["i-a"])
