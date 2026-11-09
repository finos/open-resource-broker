"""A storage read that fails raises StorageError on every backend.

A read must never turn a failure into "not found", an empty result, False or 0:
callers such as the Kubernetes orphan collector act on those answers.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from orb.infrastructure.storage.exceptions import StorageError

_READS = ["find_by_id", "find_all", "exists", "count"]


def _call(strategy, read: str):
    if read in ("find_by_id", "exists"):
        return getattr(strategy, read)("missing-id")
    return getattr(strategy, read)()


def _break_storage(storage_strategy, request, monkeypatch) -> None:
    """Make every read on the strategy fail the way a lost backend would."""
    backend = request.node.callspec.params["storage_strategy"]
    if backend == "dynamodb":
        storage_strategy.client_manager.dynamodb.delete_table(TableName=storage_strategy.table_name)
    elif backend == "sql":
        from sqlalchemy import text

        with storage_strategy.connection_manager.get_session() as session:
            session.execute(text("DROP TABLE entities"))
    else:

        def _boom(*_args, **_kwargs):
            raise OSError("storage unavailable")

        monkeypatch.setattr(storage_strategy, "_visible_records", _boom)
        monkeypatch.setattr(storage_strategy, "_lookup", _boom)


@pytest.mark.parametrize("read", _READS)
def test_reads_raise_when_storage_fails(storage_strategy, request, monkeypatch, read) -> None:
    _break_storage(storage_strategy, request, monkeypatch)

    with pytest.raises(StorageError):
        _call(storage_strategy, read)


@pytest.mark.parametrize("read", _READS)
def test_missing_item_is_not_an_error(storage_strategy, read) -> None:
    expected = {"find_by_id": None, "find_all": {}, "exists": False, "count": 0}[read]

    assert _call(storage_strategy, read) == expected


def test_dynamodb_read_error_chains_the_client_error(dynamodb_strategy) -> None:
    from botocore.exceptions import ClientError

    dynamodb_strategy.client_manager.dynamodb.delete_table(TableName=dynamodb_strategy.table_name)

    with pytest.raises(StorageError) as exc_info:
        dynamodb_strategy.find_by_id("x")

    assert isinstance(exc_info.value.__cause__, ClientError)


def test_dynamodb_find_all_raises_when_one_item_cannot_be_converted(dynamodb_strategy) -> None:
    for entity_id in ("a", "b", "c"):
        dynamodb_strategy.save(entity_id, {"id": entity_id, "name": entity_id})

    real = dynamodb_strategy.converter.from_dynamodb_item

    def _convert(item):
        if item.get("id") == "b":
            raise ValueError("undecodable item")
        return real(item)

    with patch.object(dynamodb_strategy.converter, "from_dynamodb_item", side_effect=_convert):
        with pytest.raises(StorageError) as exc_info:
            dynamodb_strategy.find_all()

    assert isinstance(exc_info.value.__cause__, ValueError)


# ---------------------------------------------------------------------------
# Through the request repository
# ---------------------------------------------------------------------------


def _request():
    from orb.domain.request.aggregate import Request
    from orb.domain.request.request_identifiers import RequestId
    from orb.domain.request.request_types import RequestType

    return Request(
        request_id=RequestId.generate(RequestType.ACQUIRE),
        request_type=RequestType.ACQUIRE,
        template_id="t",
        provider_type="aws",
        requested_count=1,
        provider_api="RunInstances",
    )


@pytest.fixture
def request_repository(dynamodb_strategy):
    from orb.infrastructure.storage.repositories.request_repository import RequestRepositoryImpl

    return RequestRepositoryImpl(dynamodb_strategy)


def test_list_read_skips_a_row_that_fails_validation(request_repository, dynamodb_strategy) -> None:
    good = _request()
    request_repository.save(good)
    # Decodes at storage level, but is not a valid Request
    dynamodb_strategy.save("bad-row", {"request_id": "bad-row", "requested_count": "many"})

    found = request_repository.find_all()

    assert [str(r.request_id) for r in found] == [str(good.request_id)]
    assert sum(request_repository._skipped_row_count.values()) == 1


def test_find_by_ids_raises_when_storage_is_gone(request_repository, dynamodb_strategy) -> None:
    request_id = str(_request().request_id)
    dynamodb_strategy.client_manager.dynamodb.delete_table(TableName=dynamodb_strategy.table_name)

    with pytest.raises(StorageError):
        request_repository.find_by_ids([request_id])


def test_k8s_existing_request_ids_raises_when_storage_is_gone(
    request_repository, dynamodb_strategy, monkeypatch
) -> None:
    """The orphan collector's input must fail loudly, not report "no such request"."""
    from orb.providers.k8s.registration import _build_request_id_readers

    class _Container:
        def get(self, _port):
            return request_repository

    monkeypatch.setattr("orb.infrastructure.di.container.get_container", lambda: _Container())
    _known, existing = _build_request_id_readers()
    kept = _request()
    request_repository.save(kept)
    assert existing([str(kept.request_id)]) == {str(kept.request_id)}

    dynamodb_strategy.client_manager.dynamodb.delete_table(TableName=dynamodb_strategy.table_name)

    with pytest.raises(StorageError):
        existing([str(kept.request_id)])
