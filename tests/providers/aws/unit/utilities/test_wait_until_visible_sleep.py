"""wait_until_visible resolves its sleep at call time."""

from __future__ import annotations

from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from orb.providers.aws.utilities import terminate_instances as module


def _not_found(instance_id: str) -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "InvalidInstanceID.NotFound",
                "Message": f"The instance ID '{instance_id}' does not exist",
            }
        },
        "DescribeInstances",
    )


def test_injected_sleep_is_used_between_attempts():
    ec2 = MagicMock()
    ec2.describe_instances.side_effect = _not_found("i-0abc")
    sleeps: list[float] = []

    module.wait_until_visible(ec2, ["i-0abc"], attempts=3, delay=2.0, sleep=sleeps.append)

    assert sleeps == [2.0, 2.0]


def test_default_sleep_is_looked_up_when_called(monkeypatch):
    ec2 = MagicMock()
    ec2.describe_instances.side_effect = _not_found("i-0abc")
    sleeps: list[float] = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    module.wait_until_visible(ec2, ["i-0abc"], attempts=2, delay=1.5)

    assert sleeps == [1.5]
