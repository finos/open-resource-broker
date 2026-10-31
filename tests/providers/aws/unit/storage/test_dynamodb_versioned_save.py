"""DynamoDB storage: version-checked saves, conflict mapping and deferred baselines.

Runs against moto. moto evaluates condition expressions and reports
cancellation reasons, which these tests verify first; real DynamoDB atomicity is
not provable here.
"""

from __future__ import annotations

import pytest

moto = pytest.importorskip("moto")

from botocore.exceptions import ClientError  # noqa: E402

from orb.domain.base.exceptions import ConcurrencyError  # noqa: E402
from orb.domain.request.aggregate import Request  # noqa: E402
from orb.domain.request.request_types import RequestType  # noqa: E402
from orb.infrastructure.adapters.logging_adapter import LoggingAdapter  # noqa: E402
from orb.infrastructure.storage.exceptions import StorageError, TransactionError  # noqa: E402
from orb.providers.aws.storage.components.dynamodb_transaction_manager import (  # noqa: E402
    DynamoDBTransactionManager,
)
from orb.providers.aws.storage.unit_of_work import DynamoDBUnitOfWork  # noqa: E402


@pytest.fixture
def uow():
    with moto.mock_aws():
        yield DynamoDBUnitOfWork(
            aws_client=None, logger=LoggingAdapter("test.dynamo.versioned"), region="us-east-1"
        )


@pytest.fixture
def requests_(uow):
    return uow._request_strategy


@pytest.fixture
def machines(uow):
    return uow._machine_strategy


def _raw_put(strategy, entity_id: str, **attributes) -> None:
    """Write an item exactly as given, bypassing the converter's version handling."""
    table = strategy.client_manager.get_table(strategy.table_name)
    table.put_item(Item={"id": entity_id, **attributes})


def _new_request() -> Request:
    return Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tmpl-1",
        machine_count=1,
        provider_type="aws",
        provider_name="aws-default",
        provider_api="RunInstances",
    )


class _RecordingClient:
    """Wraps the low-level client to record or fail TransactWriteItems calls."""

    def __init__(self, real, fail_on_call=None, reasons=None):
        self._real = real
        self.calls: list[list[dict]] = []
        self._fail_on_call = fail_on_call
        self._reasons = reasons

    def transact_write_items(self, **kwargs):
        self.calls.append(kwargs["TransactItems"])
        if self._fail_on_call == len(self.calls):
            raise ClientError(
                {
                    "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
                    "CancellationReasons": self._reasons,
                },
                "TransactWriteItems",
            )
        return self._real.transact_write_items(**kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _instrument(uow, **kwargs) -> _RecordingClient:
    manager = uow._transactions.client_manager
    client = _RecordingClient(manager.dynamodb, **kwargs)
    manager.get_client = lambda: client  # type: ignore[method-assign]
    return client


class TestMotoReportsCancellationReasons:
    """The conflict mapping rests on this; verify it before anything else."""

    def test_failed_condition_is_reported_per_action(self, uow, requests_):
        requests_.save("r-1", {"version": 5})
        client = uow._transactions.client_manager.get_client()
        manager = uow._transactions
        manager.begin_transaction()
        manager.add_put_item(
            requests_.table_name,
            {"id": "r-1", "version": 6},
            condition_expression="attribute_exists(#pk) AND #v = :e",
            expression_attribute_names={"#pk": "id", "#v": "version"},
            expression_attribute_values={":e": 1},
            key={"id": "r-1"},
        )

        with pytest.raises(ClientError) as exc_info:
            client.transact_write_items(TransactItems=manager._ordered_actions())
        manager.rollback_transaction()

        reasons = exc_info.value.response["CancellationReasons"]
        assert [r["Code"] for r in reasons] == ["ConditionalCheckFailed"]


class TestConditionalPut:
    def test_matching_baseline_writes_the_next_version(self, requests_):
        requests_.save_versioned("r-1", {"version": 0}, None)

        assert requests_.save_versioned("r-1", {"version": 0, "v": "a"}, 0) == 1
        assert requests_.save_versioned("r-1", {"version": 1, "v": "b"}, 1) == 2
        assert requests_.find_by_id("r-1")["version"] == 2  # type: ignore[index]

    def test_stale_baseline_raises_and_leaves_the_item(self, requests_):
        requests_.save_versioned("r-1", {"version": 3, "v": "theirs"}, None)

        with pytest.raises(ConcurrencyError) as exc_info:
            requests_.save_versioned("r-1", {"version": 1, "v": "mine"}, 2)

        assert "r-1" in str(exc_info.value)
        stored = requests_.find_by_id("r-1")
        assert stored is not None and stored["v"] == "theirs" and stored["version"] == 3

    def test_deleted_item_with_a_baseline_is_a_conflict(self, requests_):
        with pytest.raises(ConcurrencyError):
            requests_.save_versioned("gone", {"version": 1}, 1)
        with pytest.raises(ConcurrencyError):
            requests_.save_versioned("gone", {"version": 0}, 0)
        assert requests_.find_by_id("gone") is None

    def test_baseline_less_save_is_unconditional(self, requests_):
        requests_.save_versioned("r-1", {"version": 7, "v": "theirs"}, None)

        written = requests_.save_versioned("r-1", {"version": 0, "v": "mine"}, None)

        assert written == 0
        assert requests_.find_by_id("r-1")["v"] == "mine"  # type: ignore[index]

    def test_unstamped_item_matches_baseline_zero(self, requests_):
        _raw_put(requests_, "legacy", v="old")

        assert requests_.save_versioned("legacy", {"version": 0, "v": "new"}, 0) == 1

    def test_null_version_item_matches_baseline_zero(self, requests_):
        _raw_put(requests_, "nulled", version=None, v="old")

        assert requests_.save_versioned("nulled", {"version": 0, "v": "new"}, 0) == 1
        assert requests_.find_by_id("nulled")["version"] == 1  # type: ignore[index]

    def test_unstamped_item_does_not_match_a_positive_baseline(self, requests_):
        _raw_put(requests_, "legacy", v="old")

        with pytest.raises(ConcurrencyError):
            requests_.save_versioned("legacy", {"version": 1}, 1)

    def test_written_items_always_carry_an_integer_version(self, requests_):
        requests_.save_versioned("r-1", {"v": "no version key"}, None)

        assert requests_.find_by_id("r-1")["version"] == 0  # type: ignore[index]

    def test_other_client_errors_stay_storage_errors(self, requests_, monkeypatch):
        def broken(*_a, **_k):
            raise ClientError({"Error": {"Code": "ValidationException", "Message": "x"}}, "PutItem")

        monkeypatch.setattr(requests_.client_manager, "put_item_conditional", broken)

        with pytest.raises(StorageError):
            requests_.save_versioned("r-1", {"version": 1}, 1)

    def test_plain_save_stays_unconditional(self, requests_):
        requests_.save("r-1", {"version": 9})
        requests_.save("r-1", {"version": 1})

        assert requests_.find_by_id("r-1")["version"] == 1  # type: ignore[index]


class TestBatchOutsideAUnitOfWork:
    def test_batch_is_conditional_and_atomic(self, requests_):
        requests_.save("a", {"version": 1})
        requests_.save("b", {"version": 5})

        with pytest.raises(ConcurrencyError):
            requests_.save_versioned_batch(
                [("a", {"version": 1, "v": "x"}, 1), ("b", {"version": 5, "v": "y"}, 4)]
            )

        assert "v" not in requests_.find_by_id("a")  # type: ignore[operator]
        assert "v" not in requests_.find_by_id("b")  # type: ignore[operator]

    def test_batch_writes_all_items_in_one_call(self, uow, requests_):
        client = _instrument(uow)
        requests_.save("a", {"version": 1})

        versions = requests_.save_versioned_batch(
            [("a", {"version": 1}, 1), ("b", {"version": 0}, None)]
        )

        assert versions == [2, 0]
        assert [len(call) for call in client.calls] == [2]


class TestUnitOfWorkCommit:
    def test_conflict_surfaces_as_concurrency_error_not_transaction_error(self, uow, requests_):
        requests_.save("r-1", {"version": 1})
        requests_.save("r-1", {"version": 2})

        with pytest.raises(ConcurrencyError) as exc_info:
            with uow:
                requests_.save_versioned("r-1", {"version": 1, "v": "mine"}, 1)

        assert not isinstance(exc_info.value, TransactionError)
        assert requests_.find_by_id("r-1")["version"] == 2  # type: ignore[index]
        assert uow.in_transaction is False

    def test_conflict_on_one_item_applies_none_of_the_unit_of_work(self, uow, requests_, machines):
        requests_.save("r-1", {"version": 1})
        machines.save("m-1", {"version": 1})
        machines.save("m-1", {"version": 2})

        with pytest.raises(ConcurrencyError):
            with uow:
                requests_.save_versioned("r-1", {"version": 1, "v": "x"}, 1)
                machines.save_versioned("m-1", {"version": 1, "v": "y"}, 1)

        assert "v" not in requests_.find_by_id("r-1")  # type: ignore[operator]

    def test_two_saves_of_one_baseline_keep_the_first_condition_and_last_payload(
        self, uow, requests_
    ):
        requests_.save("r-1", {"version": 1})

        with uow:
            requests_.save_versioned("r-1", {"version": 1, "v": "first"}, 1)
            requests_.save_versioned("r-1", {"version": 2, "v": "second"}, 1)

        stored = requests_.find_by_id("r-1")
        assert stored is not None and stored["v"] == "second" and stored["version"] == 2

    def test_two_lineages_of_one_row_are_a_concurrency_error(self, uow, requests_):
        requests_.save("r-1", {"version": 2})

        with pytest.raises(ConcurrencyError):
            with uow:
                requests_.save_versioned("r-1", {"version": 1}, 1)
                requests_.save_versioned("r-1", {"version": 1}, 2)

    def test_merged_action_keeps_its_conflict_classification(self, uow, requests_):
        manager = uow._transactions
        requests_.save("r-1", {"version": 2})
        manager.begin_transaction()
        requests_.save_versioned("r-1", {"version": 1}, 1)
        first = manager.transaction_items[0]
        requests_.save_versioned("r-1", {"version": 1, "v": "second"}, 1)
        replacement = manager.transaction_items[0]

        assert replacement is not first
        assert manager._version_conditioned == {id(replacement)}

        with pytest.raises(ConcurrencyError):
            manager.commit_transaction()
        assert manager._version_conditioned == set()

    def test_rollback_leaves_the_table_unchanged_and_the_holder_in_place(self, uow):
        request = _new_request()
        with uow:
            uow.requests.save(request)
        loaded = uow.requests.find_by_id(str(request.request_id))
        assert loaded is not None
        baseline = loaded.persisted_revision.value

        uow.begin()
        uow.requests.save(loaded.start_processing())
        assert loaded.persisted_revision.value == baseline
        uow.rollback()

        assert loaded.persisted_revision.value == baseline
        stored = uow.requests.find_by_id(str(request.request_id))
        assert stored is not None and stored.persisted_revision.value == baseline

    def test_holder_moves_only_after_the_commit(self, uow):
        request = _new_request()
        uow.begin()
        uow.requests.save(request)
        assert request.persisted_revision.value is None
        uow.commit()

        assert request.persisted_revision.value == request.version

    def test_holder_does_not_move_when_the_commit_conflicts(self, uow):
        request = _new_request()
        with uow:
            uow.requests.save(request)
        mine = uow.requests.find_by_id(str(request.request_id))
        theirs = uow.requests.find_by_id(str(request.request_id))
        assert mine is not None and theirs is not None
        uow.requests.save(theirs.start_processing())
        baseline = mine.persisted_revision.value

        with pytest.raises(ConcurrencyError):
            with uow:
                uow.requests.save(mine.start_processing())

        assert mine.persisted_revision.value == baseline

    def test_callback_queue_runs_once_for_three_strategies_on_a_shared_manager(self, uow):
        ran: list[str] = []
        with uow:
            for name, strategy in (
                ("machines", uow._machine_strategy),
                ("requests", uow._request_strategy),
                ("templates", uow._template_strategy),
            ):
                strategy.save_versioned(f"{name}-1", {"version": 0}, None)
                strategy.defer_until_commit(lambda name=name: ran.append(name))
            assert ran == []

        assert sorted(ran) == ["machines", "requests", "templates"]

    def test_callbacks_do_not_run_on_rollback_or_failed_commit(self, uow, requests_):
        ran: list[int] = []
        requests_.save("r-1", {"version": 3})

        uow.begin()
        requests_.defer_until_commit(lambda: ran.append(1))
        uow.rollback()
        with pytest.raises(ConcurrencyError):
            with uow:
                requests_.save_versioned("r-1", {"version": 1}, 1)
                requests_.defer_until_commit(lambda: ran.append(2))

        assert ran == []

    def test_callback_runs_immediately_outside_a_transaction(self, requests_):
        ran: list[int] = []
        requests_.defer_until_commit(lambda: ran.append(1))

        assert ran == [1]


def _stage_claim(uow, request_version: int, machines: int, machine_version: int = 1) -> None:
    uow._request_strategy.save_versioned("r-1", {"version": request_version + 1}, request_version)
    for i in range(machines):
        uow._machine_strategy.save_versioned(f"m-{i}", {"version": machine_version + 1}, 1)


def _seed_claim(uow, machines: int) -> None:
    uow._request_strategy.save("r-1", {"version": 1})
    uow._machine_strategy.save_batch({f"m-{i}": {"version": 1} for i in range(machines)})


class TestChunkedCommit:
    def test_stale_request_row_in_the_first_call_is_a_clean_conflict(self, uow):
        _seed_claim(uow, 150)
        uow._request_strategy.save("r-1", {"version": 2})
        client = _instrument(uow)

        with pytest.raises(ConcurrencyError):
            with uow:
                _stage_claim(uow, 1, 150)

        assert len(client.calls) == 1  # the request row (first call) stopped the commit
        machines = uow._machine_strategy.find_all()
        assert all(m["version"] == 1 for m in machines.values())
        assert uow._request_strategy.find_by_id("r-1")["version"] == 2  # type: ignore[index]

    def test_stale_machine_row_in_a_later_call_is_not_retryable(self, uow):
        _seed_claim(uow, 150)
        uow._machine_strategy.save("m-120", {"version": 2})

        with pytest.raises(TransactionError) as exc_info:
            with uow:
                _stage_claim(uow, 1, 150)

        error = exc_info.value
        assert not isinstance(error, ConcurrencyError)
        assert "earlier call(s) were applied" in error.error_code
        assert "version conflict" in error.error_code
        # the request row was already advanced by the first call
        assert uow._request_strategy.find_by_id("r-1")["version"] == 2  # type: ignore[index]

    def test_no_retry_and_conflict_wins_over_a_concurrent_transaction(self, uow, requests_):
        requests_.save("r-1", {"version": 2})
        client = _instrument(
            uow,
            fail_on_call=1,
            reasons=[
                {"Code": "ConditionalCheckFailed", "Message": "x"},
                {"Code": "TransactionConflict", "Message": "y"},
            ],
        )

        with pytest.raises(ConcurrencyError):
            with uow:
                requests_.save_versioned("r-1", {"version": 1}, 1)
                requests_.save_versioned("r-2", {"version": 0}, None)

        # a failed version condition is permanent: it is sent once, not retried
        assert len(client.calls) == 1


class TestConsistentBaselineReads:
    def test_reads_that_supply_the_baseline_are_strongly_consistent(self, uow, requests_):
        seen: list[tuple[str, bool | None]] = []

        def record(params, model, **_kwargs):
            seen.append((model.name, params.get("ConsistentRead")))

        manager = requests_.client_manager
        manager.dynamodb_resource.meta.client.meta.events.register(
            "provide-client-params.dynamodb.*", record
        )
        requests_.save("r-1", {"version": 1, "status": "a"})

        requests_.find_by_id("r-1")
        requests_.exists("r-1")
        requests_.find_all()
        requests_.find_by_criteria({"status": "a"})

        reads = [(name, flag) for name, flag in seen if name in {"GetItem", "Scan"}]
        assert {name for name, _ in reads} == {"GetItem", "Scan"}
        assert all(flag is True for _, flag in reads)


class TestTransactionManagerClassification:
    def test_condition_failure_on_an_unregistered_action_is_not_a_conflict(self, uow):
        manager = uow._transactions
        client = _instrument(
            uow,
            fail_on_call=1,
            reasons=[{"Code": "ConditionalCheckFailed", "Message": "x"}],
        )
        manager.begin_transaction()
        manager.add_put_item(
            "machines",
            {"id": "m-1"},
            condition_expression="attribute_not_exists(#pk)",
            expression_attribute_names={"#pk": "id"},
            key={"id": "m-1"},
        )

        with pytest.raises(TransactionError) as exc_info:
            manager.commit_transaction()

        assert not isinstance(exc_info.value, ConcurrencyError)
        assert len(client.calls) == 1

    def test_manager_is_a_transaction_manager(self, uow):
        assert isinstance(uow._transactions, DynamoDBTransactionManager)
