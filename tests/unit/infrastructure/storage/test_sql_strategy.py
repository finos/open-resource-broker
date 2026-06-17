"""Tests for SQLStorageStrategy and SQLUnitOfWork construction + round-trip."""

import pytest


@pytest.mark.unit
class TestSQLQueryBuilderConcrete:
    """SQLQueryBuilder must not be abstract."""

    def test_can_instantiate_sql_query_builder(self):
        from orb.infrastructure.storage.components.sql_query_builder import (
            SQLQueryBuilder,
        )

        builder = SQLQueryBuilder("test_table", {"id": "TEXT PRIMARY KEY"})
        assert builder is not None
        assert builder.table_name == "test_table"


@pytest.mark.unit
class TestSQLStorageStrategyConstruction:
    """SQLStorageStrategy must construct without abstract-method errors."""

    def test_can_instantiate_with_sqlite_memory(self):
        from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

        strategy = SQLStorageStrategy(
            config={"type": "sqlite", "name": ":memory:"},
            table_name="entities",
            columns={"id": "TEXT PRIMARY KEY", "data": "TEXT"},
        )
        assert strategy is not None
        assert strategy.table_name == "entities"

    def test_save_and_load_entity(self):
        from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

        strategy = SQLStorageStrategy(
            config={"type": "sqlite", "name": ":memory:"},
            table_name="entities",
            columns={"id": "TEXT PRIMARY KEY", "data": "TEXT"},
        )

        strategy.save("e1", {"id": "e1", "data": "hello"})
        loaded = strategy.find_by_id("e1")
        assert loaded is not None
        assert loaded.get("id") == "e1"


@pytest.mark.unit
class TestSQLUnitOfWorkConstruction:
    """SQLUnitOfWork must wire up all three repositories without errors."""

    def test_construct_with_sqlite_engine(self):
        from sqlalchemy import create_engine

        from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork

        engine = create_engine("sqlite:///:memory:")
        uow = SQLUnitOfWork(engine)

        assert uow.machines is not None
        assert uow.requests is not None
        assert uow.templates is not None
