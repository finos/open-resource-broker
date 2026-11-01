"""Regression tests for https://github.com/open-resource-broker/open-resource-broker/issues/692

SQL storage: creating a request fails with
``UNIQUE constraint failed: requests.request_id`` when the same request is
saved more than once.

Root cause
----------
``SQLUnitOfWork._cols()`` flattened every ORM column type to plain ``TEXT``,
so ``SQLStorageStrategy._get_id_column()`` fell back to a non-existent ``id``
column.  Every ``exists()`` check then returned ``False`` (the ``WHERE id=?``
query errored and the error was swallowed), and every ``save()`` took the
INSERT path.  Saving the same request a second time therefore hit the
UNIQUE constraint on ``requests.request_id``.

Fix
---
``_cols()`` now emits ``TEXT PRIMARY KEY`` for the ORM column marked
``primary_key=True`` so the storage layer resolves the real identity column
(``request_id`` / ``machine_id`` / ``template_id``) and performs an UPDATE
(upsert) instead of a duplicate INSERT.

These tests exercise the real repository + UOW + SQLite strategy.  The UOW
config plumbing resolves SQLite relative paths from the process CWD, so the
fixtures ``chdir`` into ``tmp_path`` to keep each test fully isolated.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestStatus, RequestType
from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork


def _new_request(request_id: str) -> Request:
    return Request.create_new_request(
        request_type=RequestType("acquire"),
        template_id="tpl-001",
        machine_count=2,
        provider_type="aws",
        provider_name="aws-us-east-1",
        provider_api="RunInstances",
        request_id=request_id,
    )


@pytest.fixture
def sql_uow(tmp_path, monkeypatch):
    """UOW backed by a private SQLite file, isolated from the repo CWD."""
    monkeypatch.chdir(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'uow.db'}")
    uow = SQLUnitOfWork(engine)
    return uow


class TestRequestSqlUpsertRegression:
    def test_mutated_request_save_upserts_instead_of_duplicate_insert(self, sql_uow):
        """Saving a request, mutating it, then saving again must UPDATE the
        existing row (no UNIQUE constraint error, row count stays 1)."""
        request = _new_request("req-a8867bd4-7a8c-4401-96f2-63409ccf5588")

        sql_uow.requests.save(request)
        assert sql_uow._request_strategy.count() == 1

        request = request.start_processing()
        sql_uow.requests.save(request)

        assert sql_uow._request_strategy.count() == 1
        stored = sql_uow.requests.get_by_id(str(request.request_id))
        assert stored is not None
        assert stored.status == RequestStatus("in_progress")

    def test_second_save_of_stale_entity_is_cas_conflict_not_integrity_error(
        self, sql_uow
    ):
        """A blind re-save of an unchanged aggregate must surface the designed
        optimistic-lock ConcurrencyError instead of the pre-fix UNIQUE
        constraint IntegrityError."""
        request = _new_request("req-8e6bf339-8207-45b6-ab3a-81ee4ab8abc6")

        sql_uow.requests.save(request)
        with pytest.raises(Exception) as exc_info:
            sql_uow.requests.save(request)

        assert "ConcurrencyError" in type(exc_info.value).__name__ or "Concurrent" in str(
            exc_info.value
        )
        assert "UNIQUE" not in str(exc_info.value)
        assert sql_uow._request_strategy.count() == 1

    def test_id_column_resolved_from_orm_primary_key(self):
        """The identity column must be read from the ORM primary key, not a
        hard-coded/fallback ``id`` column."""
        from orb.infrastructure.storage.sql.models import RequestModel

        engine = create_engine("sqlite:///:memory:")
        uow = SQLUnitOfWork(engine)
        strategy = uow._request_strategy
        columns = strategy.columns

        assert columns[RequestModel.request_id.key] == "TEXT PRIMARY KEY"
        assert strategy._get_id_column() == "request_id"
