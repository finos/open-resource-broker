"""DynamoDB unit of work: writes across tables commit as one transaction.

These tests run against moto. moto evaluates transaction conditions and rejects
malformed transactions, but it cannot prove real DynamoDB atomicity,
idempotency or conflict behaviour; they check that the calls we make have the
shape that gives those guarantees.
"""

from __future__ import annotations

import pytest

moto = pytest.importorskip("moto")

from botocore.exceptions import ClientError  # noqa: E402

from orb.infrastructure.adapters.logging_adapter import LoggingAdapter  # noqa: E402
from orb.infrastructure.storage.base.unit_of_work import BaseUnitOfWork  # noqa: E402
from orb.infrastructure.storage.exceptions import TransactionError  # noqa: E402
from orb.providers.aws.storage.unit_of_work import DynamoDBUnitOfWork  # noqa: E402


@pytest.fixture
def uow():
    with moto.mock_aws():
        yield DynamoDBUnitOfWork(
            aws_client=None, logger=LoggingAdapter("test.dynamo.atomic"), region="us-east-1"
        )


@pytest.fixture
def api_calls(uow):
    """Record every DynamoDB API operation issued via client or resource."""
    calls: list[str] = []

    def record(model, **_kwargs):
        calls.append(model.name)

    manager = uow._machine_strategy.client_manager
    manager.dynamodb.meta.events.register("before-call.dynamodb.*", record)
    uow._transactions.client_manager.dynamodb.meta.events.register("before-call.dynamodb.*", record)
    manager.dynamodb_resource.meta.client.meta.events.register("before-call.dynamodb.*", record)
    return calls


def _all_ids(strategy) -> set[str]:
    return set(strategy.find_all().keys())


def _write_all_tables(uow, suffix: str = "1") -> None:
    uow._machine_strategy.save(f"m-{suffix}", {"machine_id": f"m-{suffix}", "status": "running"})
    uow._request_strategy.save(f"r-{suffix}", {"request_id": f"r-{suffix}", "status": "pending"})
    uow._template_strategy.save(f"t-{suffix}", {"template_id": f"t-{suffix}"})


def test_commit_issues_one_transaction_spanning_all_three_tables(uow, api_calls):
    tables = {
        uow._machine_strategy.table_name,
        uow._request_strategy.table_name,
        uow._template_strategy.table_name,
    }
    captured: list[list[dict]] = []
    client = uow._transactions.client_manager.get_client()
    client.meta.events.register(
        "provide-client-params.dynamodb.TransactWriteItems",
        lambda params, **_kw: captured.append(params["TransactItems"]),
    )
    api_calls.clear()

    with uow:
        _write_all_tables(uow)
        assert api_calls == []

    assert api_calls == ["TransactWriteItems"]
    assert len(captured) == 1
    assert {next(iter(item.values()))["TableName"] for item in captured[0]} == tables


def test_staged_writes_are_not_visible_until_commit(uow):
    uow.begin()
    _write_all_tables(uow)
    assert _all_ids(uow._machine_strategy) == set()
    assert _all_ids(uow._request_strategy) == set()
    assert _all_ids(uow._template_strategy) == set()

    uow.commit()
    assert _all_ids(uow._machine_strategy) == {"m-1"}
    assert _all_ids(uow._request_strategy) == {"r-1"}
    assert _all_ids(uow._template_strategy) == {"t-1"}


def test_exception_in_body_writes_nothing(uow):
    with pytest.raises(ValueError):
        with uow:
            _write_all_tables(uow)
            int("not a number")

    assert _all_ids(uow._machine_strategy) == set()
    assert _all_ids(uow._request_strategy) == set()
    assert _all_ids(uow._template_strategy) == set()


def test_cancelled_transaction_leaves_every_table_unchanged(uow):
    # An item already exists in the request table; a conditional create on it
    # is cancelled, which must also discard the machine and template writes.
    uow._request_strategy.save("r-1", {"request_id": "r-1", "status": "original"})

    with pytest.raises(TransactionError) as exc_info:
        with uow:
            uow._machine_strategy.save("m-1", {"machine_id": "m-1"})
            uow._template_strategy.save("t-1", {"template_id": "t-1"})
            uow._transactions.add_put_item(
                uow._request_strategy.table_name,
                {"id": "r-1", "status": "replaced"},
                condition_expression="attribute_not_exists(id)",
                key={"id": "r-1"},
            )

    assert isinstance(exc_info.value.__cause__, ClientError)
    assert "ConditionalCheckFailed" in exc_info.value.error_code
    assert _all_ids(uow._machine_strategy) == set()
    assert _all_ids(uow._template_strategy) == set()
    assert uow._request_strategy.find_by_id("r-1")["status"] == "original"


def test_save_then_delete_of_same_item_in_one_unit_of_work(uow):
    uow._machine_strategy.save("m-1", {"machine_id": "m-1", "status": "old"})

    with uow:
        uow._machine_strategy.save("m-1", {"machine_id": "m-1", "status": "new"})
        uow._machine_strategy.delete("m-1")
        uow._machine_strategy.save("m-2", {"machine_id": "m-2"})
        uow._machine_strategy.save("m-2", {"machine_id": "m-2", "status": "final"})

    assert _all_ids(uow._machine_strategy) == {"m-2"}
    assert uow._machine_strategy.find_by_id("m-2")["status"] == "final"


class _RecordingClient:
    """Wraps the real client, recording TransactWriteItems calls and optionally failing one."""

    def __init__(self, real, fail_on_call: int | None = None) -> None:
        self._real = real
        self._fail_on = fail_on_call
        self.calls: list[list[dict]] = []

    def transact_write_items(self, **kwargs):
        self.calls.append(kwargs["TransactItems"])
        if self._fail_on == len(self.calls):
            raise ClientError(
                {"Error": {"Code": "ValidationException", "Message": "injected"}},
                "TransactWriteItems",
            )
        return self._real.transact_write_items(**kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _instrument(uow, fail_on_call: int | None = None) -> _RecordingClient:
    manager = uow._transactions.client_manager
    client = _RecordingClient(manager.dynamodb, fail_on_call)
    manager.get_client = lambda: client  # type: ignore[method-assign]
    return client


def _stage_claim(uow, machines: int) -> None:
    """Request row plus its machines, the shape of a large provisioning request."""
    uow._request_strategy.save("r-1", {"request_id": "r-1", "status": "complete"})
    uow._machine_strategy.save_batch(
        {f"m-{i}": {"machine_id": f"m-{i}", "request_id": "r-1"} for i in range(machines)}
    )


@pytest.mark.parametrize("machines", [150, 1000])
def test_large_unit_of_work_persists_everything_in_bounded_calls(uow, machines):
    client = _instrument(uow)

    with uow:
        _stage_claim(uow, machines)

    assert len(_all_ids(uow._machine_strategy)) == machines
    assert _all_ids(uow._request_strategy) == {"r-1"}
    assert all(len(call) <= 100 for call in client.calls)
    assert sum(len(call) for call in client.calls) == machines + 1
    # the request row is written in the first call
    first_tables = {next(iter(a.values()))["TableName"] for a in client.calls[0]}
    assert uow._request_strategy.table_name in first_tables


def test_failure_of_a_later_call_on_writes_leaves_request_and_a_machine_prefix(uow):
    client = _instrument(uow, fail_on_call=2)

    with pytest.raises(TransactionError) as exc_info:
        with uow:
            _stage_claim(uow, 150)

    message = exc_info.value.error_code
    assert "call 2 of 2" in message and "1 earlier call(s) were applied" in message
    assert "r-1" not in _all_ids(uow._machine_strategy)
    assert _all_ids(uow._request_strategy) == {"r-1"}
    assert len(_all_ids(uow._machine_strategy)) == len(client.calls[0]) - 1
    assert uow.in_transaction is False


def test_large_delete_removes_machines_first_and_request_last(uow):
    with uow:
        _stage_claim(uow, 150)
    client = _instrument(uow)

    with uow:
        uow._machine_strategy.delete_batch([f"m-{i}" for i in range(150)])
        uow._request_strategy.delete("r-1")

    assert _all_ids(uow._machine_strategy) == set()
    assert _all_ids(uow._request_strategy) == set()
    last = client.calls[-1][-1]
    assert next(iter(last)) == "Delete"
    assert next(iter(last.values()))["TableName"] == uow._request_strategy.table_name


def test_failure_of_a_later_call_on_deletes_keeps_the_request(uow):
    with uow:
        _stage_claim(uow, 150)
    _instrument(uow, fail_on_call=2)

    with pytest.raises(TransactionError):
        with uow:
            uow._machine_strategy.delete_batch([f"m-{i}" for i in range(150)])
            uow._request_strategy.delete("r-1")

    assert _all_ids(uow._request_strategy) == {"r-1"}
    assert len(_all_ids(uow._machine_strategy)) == 150 - 100


def test_up_to_one_hundred_actions_remain_a_single_atomic_call(uow):
    client = _instrument(uow)

    with uow:
        uow._machine_strategy.save_batch({f"m-{i}": {"machine_id": f"m-{i}"} for i in range(100)})

    assert [len(c) for c in client.calls] == [100]


def test_failure_within_a_single_call_applies_nothing(uow):
    _instrument(uow, fail_on_call=1)

    with pytest.raises(TransactionError):
        with uow:
            _stage_claim(uow, 50)

    assert _all_ids(uow._machine_strategy) == set()
    assert _all_ids(uow._request_strategy) == set()


def test_direct_writes_outside_a_unit_of_work_are_unchanged(uow, api_calls):
    uow._machine_strategy.save("m-1", {"machine_id": "m-1"})

    assert api_calls == ["PutItem"]
    assert _all_ids(uow._machine_strategy) == {"m-1"}


def test_failed_commit_does_not_wedge_the_unit_of_work(uow):
    assert isinstance(uow, BaseUnitOfWork)
    uow.begin()
    uow._transactions.add_put_item(
        uow._request_strategy.table_name,
        {"id": "x"},
        condition_expression="attribute_exists(id)",
        key={"id": "x"},
    )
    with pytest.raises(TransactionError):
        uow.commit()
    assert uow.in_transaction is False


@pytest.mark.asyncio
async def test_status_service_saves_150_machines_through_dynamodb(uow):
    """A fleet larger than one transaction is stored in full via bounded units of work."""
    from unittest.mock import MagicMock

    from orb.application.services.provisioning_orchestration_service import ProvisioningResult
    from orb.application.services.request_status_management_service import (
        RequestStatusManagementService,
    )
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestType

    class Factory:
        def create_unit_of_work(self):
            return DynamoDBUnitOfWork(
                aws_client=None,
                logger=LoggingAdapter("test.dynamo.fleet"),
                region="us-east-1",
            )

    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tmpl-001",
        machine_count=150,
        provider_type="aws",
        provider_name="aws-prod",
        provider_api="EC2Fleet",
    )
    instances = [
        {"instance_id": f"i-{i:017x}", "instance_type": "t3.medium", "image_id": "ami-1"}
        for i in range(150)
    ]
    result = ProvisioningResult(
        success=True,
        resource_ids=["fleet-1"],
        machine_ids=[i["instance_id"] for i in instances],
        instances=instances,
        provider_data={},
        fulfilled_count=150,
        is_final=True,
    )

    svc = RequestStatusManagementService(uow_factory=Factory(), logger=MagicMock())
    await svc.update_request_from_provisioning(request, result)

    assert len(_all_ids(uow._machine_strategy)) == 150
