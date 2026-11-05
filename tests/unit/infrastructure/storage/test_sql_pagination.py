"""Keyset page queries: per-dialect SQL, pushdown limits, and the page-size guarantee."""

from __future__ import annotations

import re
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects import mysql, postgresql, sqlite

from orb.domain.base.pagination import SortSpec, sort_text
from orb.infrastructure.storage.sql.entities import entity_columns
from orb.infrastructure.storage.sql.pagination import (
    build_count_statement,
    build_page_statement,
    supports_filters,
    supports_sort,
)
from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork

REQUEST_COLUMNS = list(entity_columns("requests"))
MACHINE_COLUMNS = list(entity_columns("machines"))
DIALECTS = {
    "postgresql": postgresql.dialect(),
    "mysql": mysql.dialect(),
    "sqlite": sqlite.dialect(),
}


def _sql(dialect: str, **overrides: Any) -> str:
    params: dict[str, Any] = {
        "table": "requests",
        "columns": REQUEST_COLUMNS,
        "id_column": "request_id",
        "sort_column": "status",
        "descending": False,
        "criteria": {"provider_type": "aws"},
        "after": ("running", "req-1"),
        "limit": 50,
        "dialect": dialect,
    }
    params.update(overrides)
    statement = build_page_statement(**params)
    assert statement is not None
    return " ".join(str(statement.compile(dialect=DIALECTS[dialect])).split())


@pytest.mark.unit
class TestDialectSpecificOrdering:
    def test_postgresql_uses_the_c_collation_on_order_and_predicate(self) -> None:
        # SQLAlchemy versions differ in whether they also cast and collate the bound
        # parameters ("%(x)s::VARCHAR COLLATE "C""); only the column expressions matter
        sql = _sql("postgresql")

        key = r'coalesce\(requests\.status, %\(coalesce_1\)s(?:::VARCHAR)?\)\)? COLLATE "C"'
        ident = r'\(?requests\.request_id COLLATE "C"'
        assert len(re.findall(key, sql)) == 3  # predicate ">" and "=", ORDER BY
        assert len(re.findall(ident, sql)) == 2  # predicate, ORDER BY
        assert '(requests.provider_type COLLATE "C") = ' in sql  # the filter column
        assert re.search(key + r" ASC, requests\.request_id COLLATE \"C\" ASC", sql)

    def test_mysql_compares_bytes_not_the_case_insensitive_default(self) -> None:
        sql = _sql("mysql")

        assert "CAST(coalesce(requests.status, %s) AS BINARY) >" in sql
        assert "CAST(coalesce(requests.status, %s) AS BINARY) =" in sql
        assert "CAST(requests.request_id AS BINARY) >" in sql
        assert "ORDER BY CAST(coalesce(requests.status, %s) AS BINARY) ASC" in sql

    def test_sqlite_needs_no_wrapper(self) -> None:
        sql = _sql("sqlite")

        assert "COLLATE" not in sql and "CAST" not in sql
        assert "ORDER BY coalesce(requests.status, ?) ASC, requests.request_id ASC" in sql

    def test_mysql_equality_filters_compare_bytes_in_page_and_count(self) -> None:
        page = _sql("mysql", criteria={"provider_name": "aws-a"})
        count = build_count_statement(
            table="requests",
            columns=REQUEST_COLUMNS,
            criteria={"provider_name": "aws-a"},
            dialect="mysql",
        )
        assert count is not None

        assert "CAST(requests.provider_name AS BINARY) = %s" in page
        assert "CAST(requests.provider_name AS BINARY) = %s" in str(
            count.compile(dialect=DIALECTS["mysql"])
        )

    def test_postgresql_and_sqlite_equality_filters(self) -> None:
        assert '(requests.provider_name COLLATE "C") = ' in _sql(
            "postgresql", criteria={"provider_name": "aws-a"}
        )
        assert "requests.provider_name = ?" in _sql("sqlite", criteria={"provider_name": "aws-a"})

    @pytest.mark.parametrize("dialect", list(DIALECTS))
    def test_predicate_is_expanded_not_a_row_value_comparison(self, dialect: str) -> None:
        sql = _sql(dialect)

        where = sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
        assert " OR " in where and " AND " in where
        assert ", requests.request_id) >" not in where  # no (a, b) > (c, d)

    @pytest.mark.parametrize("dialect", list(DIALECTS))
    def test_descending_flips_both_comparisons_and_both_sort_keys(self, dialect: str) -> None:
        sql = _sql(dialect, descending=True)

        where, order = sql.split("WHERE", 1)[1].split("ORDER BY", 1)
        assert ">" not in where and "<" in where
        assert order.count("DESC") == 2 and "ASC" not in order

    @pytest.mark.parametrize("dialect", list(DIALECTS))
    def test_missing_sort_values_are_the_empty_string(self, dialect: str) -> None:
        assert "coalesce(requests.status" in _sql(dialect)

    @pytest.mark.parametrize("dialect", list(DIALECTS))
    def test_first_page_has_no_cursor_predicate(self, dialect: str) -> None:
        sql = _sql(dialect, after=None)

        assert "after_value" not in sql and " OR " not in sql

    @pytest.mark.parametrize("dialect", list(DIALECTS))
    def test_one_extra_row_is_requested(self, dialect: str) -> None:
        statement = build_page_statement(
            table="requests",
            columns=REQUEST_COLUMNS,
            id_column="request_id",
            sort_column="status",
            descending=False,
            criteria={},
            after=None,
            limit=50,
            dialect=dialect,
        )
        assert statement is not None
        assert statement.compile(dialect=DIALECTS[dialect]).params["param_1"] == 51


@pytest.mark.unit
class TestWhatCannotBePushedDown:
    def _page(self, **overrides: Any) -> Any:
        params: dict[str, Any] = {
            "table": "requests",
            "columns": REQUEST_COLUMNS,
            "id_column": "request_id",
            "sort_column": "status",
            "descending": False,
            "criteria": {},
            "after": None,
            "limit": 10,
            "dialect": "sqlite",
        }
        params.update(overrides)
        return build_page_statement(**params)

    @pytest.mark.parametrize("dialect", ["oracle", "mssql", "", "duckdb"])
    def test_unknown_dialect_falls_back_to_in_process_paging(self, dialect: str) -> None:
        assert self._page(dialect=dialect) is None

    @pytest.mark.parametrize("sort", ["provider_data", "resource_ids", "no_such", "tags; DROP"])
    def test_sort_columns_outside_the_allow_list(self, sort: str) -> None:
        assert self._page(sort_column=sort) is None

    def test_filters_outside_the_allow_list(self) -> None:
        assert self._page(criteria={"resource_ids": "x"}) is None
        assert self._page(criteria={"status; DROP TABLE requests": "x"}) is None

    def test_operator_and_non_string_filter_values(self) -> None:
        assert self._page(criteria={"status": {"$in": ["a"]}}) is None
        assert self._page(criteria={"status": 5}) is None

    def test_machine_launch_time_only_on_sqlite(self) -> None:
        assert supports_sort("machines", "launch_time", "sqlite")
        assert not supports_sort("machines", "launch_time", "postgresql")
        assert not supports_sort("machines", "launch_time", "mysql")
        assert supports_sort("machines", "created_at", "postgresql")

    def test_machine_filters_are_not_the_request_filters(self) -> None:
        assert supports_filters("machines", {"request_id": "r"})
        assert not supports_filters("machines", {"template_id": "t"})
        assert supports_filters("requests", {"template_id": "t"})

    def test_count_statement_applies_the_same_filters_without_a_cursor(self) -> None:
        statement = build_count_statement(
            table="requests",
            columns=REQUEST_COLUMNS,
            criteria={"status": "pending"},
            dialect="sqlite",
        )
        assert statement is not None
        sql = str(statement.compile(dialect=sqlite.dialect()))
        assert "count(*)" in sql.lower() and "requests.status = ?" in sql


def _request_data(index: int) -> dict[str, Any]:
    return {
        "request_id": f"req-{index:08d}-0000-4000-8000-000000000000",
        "template_id": "tmpl-1",
        "request_type": "acquire",
        "status": ["pending", "complete", "in_progress"][index % 3],
        "provider_type": "aws",
        "provider_name": "aws-default",
        "provider_api": "RunInstances",
        "requested_count": 1,
        "created_at": (
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index // 7)
        ).isoformat(),
    }


@pytest.fixture
def uow(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'orb.db'}")
    unit = SQLUnitOfWork(engine)
    yield unit
    engine.dispose()


@pytest.mark.unit
class TestFindPageOnSqlite:
    def test_pages_cover_every_row_in_text_order(self, uow: SQLUnitOfWork) -> None:
        strategy = uow.requests.storage_strategy
        data = {d["request_id"]: d for d in (_request_data(i) for i in range(40))}
        strategy.save_batch(data)
        expected = sorted(((d["created_at"], rid) for rid, d in data.items()), reverse=True)

        seen: list[tuple[str, str]] = []
        after = None
        while True:
            result = strategy.find_page({}, "created_at", True, after, 9)  # type: ignore[attr-defined]
            assert result is not None
            rows, has_more, total = result
            assert total == 40
            seen.extend((sort_key, row["request_id"]) for row, sort_key in rows)
            if not has_more:
                break
            after = seen[-1]

        assert seen == expected

    def test_null_and_empty_sort_values_sort_first_and_cursor_through(
        self, uow: SQLUnitOfWork
    ) -> None:
        strategy = uow.requests.storage_strategy
        rows = {}
        for index in range(6):
            data = _request_data(index)
            data["status"] = None if index in (1, 4) else data["status"]
            rows[data["request_id"]] = data
        strategy.save_batch(rows)

        ids: list[str] = []
        after = None
        while True:
            rows_, has_more, _ = strategy.find_page({}, "status", False, after, 2)  # type: ignore[attr-defined]
            ids.extend(row["request_id"] for row, _ in rows_)
            if not has_more:
                break
            after = (rows_[-1][1], rows_[-1][0]["request_id"])

        keyed = sorted((sort_text(d["status"]), rid) for rid, d in rows.items())
        assert ids == [rid for _, rid in keyed]
        assert ids[:2] == [k[1] for k in keyed[:2]] and keyed[0][0] == ""

    def test_filter_equality_is_case_sensitive_like_the_listing(self, uow: SQLUnitOfWork) -> None:
        strategy = uow.requests.storage_strategy
        strategy.save_batch({d["request_id"]: d for d in (_request_data(i) for i in range(6))})

        rows, has_more, total = strategy.find_page(
            {"status": "pending"}, "request_id", False, None, 10
        )  # type: ignore[attr-defined]
        none, _, none_total = strategy.find_page(
            {"status": "PENDING"}, "request_id", False, None, 10
        )  # type: ignore[attr-defined]

        assert total == 2 and len(rows) == 2 and not has_more
        assert none == [] and none_total == 0

    def test_unsupported_sort_returns_none(self, uow: SQLUnitOfWork) -> None:
        assert uow.requests.storage_strategy.find_page({}, "provider_data", False, None, 5) is None  # type: ignore[attr-defined]


@pytest.mark.unit
class TestPageSizeGuarantee:
    """First page of 50 from 20 000 rows reads at most 51 and uses a fraction of the memory."""

    ROWS = 20_000

    def _seed(self, tmp_path: Path) -> Any:
        engine = create_engine(f"sqlite:///{tmp_path / 'big.db'}")
        unit = SQLUnitOfWork(engine)
        base = _request_data(0)
        unit.requests.storage_strategy.save_batch({base["request_id"]: base})
        with engine.begin() as conn:
            columns = [c for c in REQUEST_COLUMNS if c != "request_id"]
            names = ", ".join(columns)
            conn.execute(
                text(
                    f"INSERT INTO requests (request_id, {names}) "  # noqa: S608
                    f"SELECT :new_id, {names} FROM requests WHERE request_id = :base"
                ),
                [
                    {
                        "new_id": f"req-{i:08d}-0000-4000-8000-000000000000",
                        "base": base["request_id"],
                    }
                    for i in range(1, self.ROWS)
                ],
            )
        return engine, unit

    def test_first_page_reads_at_most_limit_plus_one_rows(self, tmp_path: Path) -> None:
        engine, unit = self._seed(tmp_path)
        statements: list[tuple[str, Any]] = []

        # A listener on this test's own engine: engines shared across tests would
        # leak the listener into other tests' counts
        @event.listens_for(engine, "before_cursor_execute")
        def record(_conn, _cursor, statement, parameters, _context, _many):  # noqa: ANN001
            statements.append((statement, parameters))

        page = unit.requests.find_page({}, SortSpec("created_at", True), None, 50)

        assert page is not None and len(page.items) == 50 and page.has_more
        assert page.total_count == self.ROWS
        page_selects = [s for s, _ in statements if "LIMIT" in s.upper()]
        # The database itself stops at limit + 1 rows: the statement carries LIMIT 51
        assert len(page_selects) == 1
        limit_params = [p for s, p in statements if "LIMIT" in s.upper()][0]
        assert 51 in (limit_params if isinstance(limit_params, tuple) else limit_params.values())

    def test_peak_memory_is_a_small_fraction_of_loading_everything(self, tmp_path: Path) -> None:
        engine, unit = self._seed(tmp_path)

        tracemalloc.start()
        tracemalloc.reset_peak()
        full = unit.requests.find_all()
        _, full_peak = tracemalloc.get_traced_memory()
        del full
        tracemalloc.reset_peak()
        page = unit.requests.find_page({}, SortSpec("created_at", True), None, 50)
        _, page_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert page is not None and len(page.items) == 50
        assert page_peak < full_peak / 10
