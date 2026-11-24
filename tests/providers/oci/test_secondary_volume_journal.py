"""Restart, optimistic concurrency and negative ownership controls on real disk-backed storage."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local

import pytest
from sqlalchemy import create_engine

from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus, RequestType
from orb.domain.request.value_objects import RequestId
from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork
from orb.providers.oci.volume_journal import OCIVolumeJournal

SPECS = [
    {"size_in_gbs": 50, "delete_on_return": False},
    {"size_in_gbs": 51, "delete_on_return": True},
]


@pytest.fixture
def storage(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'orb.db'}")

    def create():
        return SQLUnitOfWork(engine)

    request = Request.create_new_request(
        RequestType.ACQUIRE,
        "e5",
        1,
        provider_type="oci",
        provider_name="oci-main",
        provider_api="OCICompute",
    )
    with create() as uow:
        uow.requests.save(request)
    yield create, request.request_id.value
    engine.dispose()


def journal(create, time=1000):
    return OCIVolumeJournal(create, "oci-main", "region-a", clock=lambda: time)


def begin(j, rid, worker="worker-a"):
    return j.begin(
        rid,
        worker,
        "compartment-a",
        "ad-a",
        SPECS,
        instance_type="VM.Standard.E5.Flex",
        image_id="image-a",
    )


def test_restart_and_mixed_retention(storage):
    create, rid = storage
    first = journal(create)
    assert begin(first, rid) is True
    assert begin(first, rid) is False
    with create() as uow:
        persisted = uow.requests.get_by_id(RequestId(value=rid))
        assert persisted.status == RequestStatus.ACQUIRING
        assert persisted.deadline_at is not None
        assert persisted.resource_ids == ["worker-a"]
    # No outer acquire result was written. A separately opened broker sees the worker.
    assert journal(create).pending_workers() == [(rid, "worker-a")]
    assert journal(create).worker_details(rid, "worker-a") == {
        "instance_type": "VM.Standard.E5.Flex",
        "image_id": "image-a",
    }
    first.accept_workers(rid, ["worker-a"])
    with pytest.raises(ValueError, match="already accepted"):
        first.start_return(rid, "worker-a", unaccepted_only=True)
    for i in (0, 1):
        first.retry_token(rid, "worker-a", i, "create")
        first.record_volume(rid, "worker-a", i, f"volume-{i}")
        first.retry_token(rid, "worker-a", i, "attach")
        first.record_attachment(rid, "worker-a", i, f"attachment-{i}")
        assert first.pending_workers() == [(rid, "worker-a")]
        first.confirm_attached(rid, "worker-a", i)
    recovered = journal(create)
    assert [(s.volume_id, s.attachment_id) for s in recovered.slots(rid, "worker-a")] == [
        ("volume-0", "attachment-0"),
        ("volume-1", "attachment-1"),
    ]
    assert recovered.pending_workers() == []
    recovered.start_return(rid, "worker-a")
    with pytest.raises(ValueError, match="policy"):
        recovered.finish(rid, "worker-a", 0, "deleted")
    recovered.finish(rid, "worker-a", 0, "retained")
    assert recovered.pending_workers() == [(rid, "worker-a")]
    journal(create).finish(rid, "worker-a", 1, "deleted")
    assert recovered.pending_workers() == []


def test_independent_writers_converge_and_conflicting_receipts_fail(storage):
    create, rid = storage
    begin(journal(create), rid)
    gate = Barrier(2)

    # Force both independent SQL transactions to read the same revision before
    # either can attempt its write. Retries read the winner's persisted value.
    state = local()

    def observed_uow():
        uow = create()
        read = uow.requests.get_by_id

        def read_at_barrier(key):
            row = read(key)
            if not getattr(state, "synchronized", False):
                state.synchronized = True
                gate.wait(timeout=10)
            return row

        uow.requests.get_by_id = read_at_barrier
        return uow

    def claim(_):
        j = journal(observed_uow)
        return j.retry_token(rid, "worker-a", 0, "create")

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.map(claim, range(2))
    assert a == b
    gate = Barrier(2)

    def save(value):
        j = journal(observed_uow)
        try:
            j.record_volume(rid, "worker-a", 0, value)
            return value
        except ValueError as exc:
            assert "Conflicting" in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.map(save, ("disk-a", "disk-b"))
    assert (a is None) != (b is None)
    assert journal(create).slots(rid, "worker-a")[0].volume_id == (a or b)


def test_expiry_rejection_and_cancellation_fences(storage):
    create, rid = storage
    first = journal(create)
    begin(first, rid)
    original = first.retry_token(rid, "worker-a", 0, "create")
    assert journal(create, 1100).retry_token(rid, "worker-a", 0, "create") == original
    with pytest.raises(ValueError, match="window"):
        journal(create, 1000 + 24 * 3600).retry_token(rid, "worker-a", 0, "create")
    first.refuse(rid, "worker-a", 0, "create")
    with pytest.raises(ValueError, match="refused"):
        first.retry_token(rid, "worker-a", 0, "create")
    first.retry_token(rid, "worker-a", 1, "create")
    with create() as uow:
        row = uow.requests.get_by_id(RequestId(value=rid))
        uow.requests.save(row.update_status(RequestStatus.CANCELLED, "cancel", force=True))
    assert first.abandoned(rid) and first.pending_workers() == [(rid, "worker-a")]
    first.record_volume(rid, "worker-a", 1, "exact-disk")  # in-flight success after cancellation
    with pytest.raises(ValueError, match="ended"):
        journal(create).retry_token(rid, "worker-a", 1, "attach")
    first.start_return(rid, "worker-a")
    with pytest.raises(ValueError, match="Missing"):
        first.finish(rid, "worker-a", 0, "skipped")
    first.finish(rid, "worker-a", 1, "deleted")


def test_region_provider_and_conflicting_slot_claims(storage):
    create, rid = storage
    j = journal(create)
    begin(j, rid)
    begin(j, rid, "worker-b")
    for worker in ("worker-a", "worker-b"):
        j.retry_token(rid, worker, 0, "create")
    j.record_volume(rid, "worker-a", 0, "original")
    with pytest.raises(ValueError, match="already owned"):
        j.record_volume(rid, "worker-b", 0, "original")
    with pytest.raises(ValueError, match="different region"):
        OCIVolumeJournal(create, "oci-main", "elsewhere").slots(rid, "worker-a")
    with pytest.raises(ValueError, match="original acquire"):
        OCIVolumeJournal(create, "other", "region-a").slots(rid, "worker-a")
    j.start_return(rid, "worker-a")
    with pytest.raises(ValueError, match="return"):
        j.retry_token(rid, "worker-a", 1, "create")
    j.finish(rid, "worker-a", 1, "skipped")


def test_daemon_resumes_exact_workers_and_continues_after_individual_failure(storage, monkeypatch):
    import asyncio
    from unittest.mock import Mock

    from orb.providers.oci.configuration.config import OCIProviderConfig
    from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy

    create, rid = storage
    j = journal(create)
    for worker in ("worker-a", "worker-b"):
        begin(j, rid, worker)
    with create() as uow:
        row = uow.requests.get_by_id(RequestId(value=rid))
        uow.requests.save(row.add_resource_id("worker-a").add_resource_id("worker-b"))
    strategy = OCIProviderStrategy(
        OCIProviderConfig(region="us-phoenix-1"), Mock(), volume_journal=j
    )
    resumed = []

    def reconcile(origin, worker):
        resumed.append((origin, worker))
        if worker == "worker-a":
            raise RuntimeError("one isolated failure")

    monkeypatch.setattr(strategy._compute_handler, "resume_volume_worker", reconcile)
    asyncio.run(strategy.start_daemon_services())
    assert sorted(resumed) == [(rid, "worker-a"), (rid, "worker-b")]


def test_unsupported_or_volatile_store_cannot_authorize_creation():
    volatile = create_engine("sqlite:///:memory:")
    try:
        j = journal(lambda: SQLUnitOfWork(volatile))
        with pytest.raises(ValueError, match="persistent"):
            j.require_acquisition("req-11111111-1111-4111-8111-111111111111")
    finally:
        volatile.dispose()


@pytest.mark.asyncio
async def test_idle_daemon_follows_only_new_exact_workers_and_stops(storage, monkeypatch):
    import asyncio
    from unittest.mock import Mock

    from orb.providers.oci.configuration.config import OCIProviderConfig
    from orb.providers.oci.strategy import oci_provider_strategy as strategy_module

    create, rid = storage
    j = journal(create)
    strategy = strategy_module.OCIProviderStrategy(
        OCIProviderConfig(region="us-phoenix-1"), Mock(), volume_journal=j
    )
    scanned = Mock(wraps=j.pending_workers)
    monkeypatch.setattr(j, "pending_workers", scanned)
    monkeypatch.setattr(strategy_module, "_VOLUME_POLL_SECONDS", 0.01)
    calls = []

    def resume(origin, worker):
        calls.append((origin, worker))
        if len(calls) == 1:
            return
        # Both slots become unambiguously skipped during a concurrent return.
        j.start_return(origin, worker)
        for index in range(2):
            j.finish(origin, worker, index, "skipped")

    monkeypatch.setattr(strategy._compute_handler, "resume_volume_worker", resume)
    try:
        await strategy.start_daemon_services()
        await strategy.start_daemon_services()
        assert scanned.call_count == 1 and strategy._volume_task is None
        with create() as uow:
            row = uow.requests.get_by_id(RequestId(value=rid))
            uow.requests.save(row.add_resource_id("worker-a"))
        await asyncio.to_thread(begin, j, rid)
        await asyncio.sleep(0)
        assert strategy._volume_task is not None
        await asyncio.wait_for(strategy._volume_task, timeout=10)
        assert calls == [(rid, "worker-a")] * 2
        assert scanned.call_count == 1
    finally:
        strategy.cleanup()
        assert j.on_change is None


def test_corrupt_terminal_record_cannot_mask_live_volume(storage):
    create, rid = storage
    j = journal(create)
    begin(j, rid)
    key = j.plan_key("worker-a") + "/0/outcome"
    with create() as uow:
        row = uow.requests.get_by_id(RequestId(value=rid))
        uow.requests.save(row.set_provider_data({**row.provider_data, key: "deleted"}))
    with pytest.raises(ValueError, match="terminal"):
        j.slots(rid, "worker-a")


def test_rollback_only_new_workers_and_cancelled_launch_receipt(storage):
    create, rid = storage
    j = journal(create)
    assert begin(j, rid)
    j.start_return(rid, "worker-a", unaccepted_only=True)
    with pytest.raises(ValueError, match="handoff"):
        j.accept_workers(rid, ["worker-a"])
    with create() as uow:
        row = uow.requests.get_by_id(RequestId(value=rid))
        uow.requests.save(row.update_status(RequestStatus.CANCELLED, "cancel", force=True))
    assert begin(j, rid, "worker-b")
    assert (rid, "worker-b") in journal(create).pending_workers()
    with pytest.raises(ValueError, match="ended"):
        j.retry_token(rid, "worker-b", 0, "create")
