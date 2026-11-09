"""The EC2 instance helpers must not add a second retry layer."""

import inspect
from unittest.mock import MagicMock

import pytest

import orb.providers.aws.utilities.ec2.instances as mod


def test_helpers_are_not_wrapped_by_a_retry_decorator():
    for name in ("_describe_instance", "_run_instance", "_terminate_instance", "_create_tags"):
        assert not hasattr(getattr(mod, name), "__wrapped__"), name
    assert "@retry" not in inspect.getsource(mod)


def test_a_failing_call_is_attempted_once():
    ec2 = MagicMock()
    ec2.describe_instances.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        mod._describe_instance(ec2, "i-1")
    assert ec2.describe_instances.call_count == 1


def test_run_instance_sends_a_client_token_per_call():
    ec2 = MagicMock()
    mod._run_instance(ec2, {"ImageId": "ami-1"})
    mod._run_instance(ec2, {"ImageId": "ami-1"})
    tokens = [call.kwargs["ClientToken"] for call in ec2.run_instances.call_args_list]
    assert len(set(tokens)) == 2


def test_run_instance_keeps_a_caller_supplied_token():
    ec2 = MagicMock()
    mod._run_instance(ec2, {"ImageId": "ami-1", "ClientToken": "mine"})
    assert ec2.run_instances.call_args.kwargs["ClientToken"] == "mine"
