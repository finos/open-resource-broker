"""Fixtures for storage strategy contract tests.

Provides parameterised `storage_strategy` and `unit_of_work` fixtures so
each backend (JSON, SQL/SQLite, DynamoDB/moto) runs the same contract tests.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest

try:
    from moto import mock_aws as _moto_mock_aws

    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False
    _moto_mock_aws = None


@contextmanager
def _mock_aws():
    if _moto_mock_aws is None:
        yield
        return
    with _moto_mock_aws():
        yield


_ENTITY_TABLE = "entities"
_ENTITY_COLUMNS = {"id": "TEXT PRIMARY KEY", "data": "TEXT", "name": "TEXT"}


# ---------------------------------------------------------------------------
# Strategy fixtures (low-level: SQLStorageStrategy / JSONStorageStrategy / DynamoDB)
# ---------------------------------------------------------------------------


@pytest.fixture
def json_strategy(tmp_path):
    from orb.infrastructure.storage.json.strategy import JSONStorageStrategy

    file_path = tmp_path / "entities.json"
    return JSONStorageStrategy(file_path=str(file_path), entity_type="entities")


@pytest.fixture
def sql_strategy():
    from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

    return SQLStorageStrategy(
        config={"type": "sqlite", "name": ":memory:"},
        table_name=_ENTITY_TABLE,
        columns=_ENTITY_COLUMNS,
    )


@pytest.fixture
def dynamodb_strategy() -> Iterator:
    if not HAS_MOTO:
        pytest.skip("moto not installed")

    from orb.providers.aws.storage.strategy import DynamoDBStorageStrategy

    with _mock_aws():
        # aws_client=None forces internal boto3 session, which moto intercepts.
        from orb.infrastructure.adapters.logging_adapter import LoggingAdapter

        strategy = DynamoDBStorageStrategy(
            logger=LoggingAdapter("test.dynamo"),
            aws_client=None,
            region="us-east-1",
            table_name=_ENTITY_TABLE,
        )
        yield strategy


@pytest.fixture(params=["json", "sql", "dynamodb"])
def storage_strategy(request, json_strategy, sql_strategy, dynamodb_strategy):
    """Parameterised strategy fixture used by contract tests."""
    return {
        "json": json_strategy,
        "sql": sql_strategy,
        "dynamodb": dynamodb_strategy,
    }[request.param]


# ---------------------------------------------------------------------------
# UnitOfWork fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def json_uow(tmp_path):
    from orb.infrastructure.storage.json.unit_of_work import JSONUnitOfWork

    return JSONUnitOfWork(data_dir=str(tmp_path))


@pytest.fixture
def sql_uow():
    from sqlalchemy import create_engine

    from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork

    engine = create_engine("sqlite:///:memory:")
    return SQLUnitOfWork(engine)


@pytest.fixture
def dynamodb_uow():
    if not HAS_MOTO:
        pytest.skip("moto not installed")

    from orb.providers.aws.storage.unit_of_work import DynamoDBUnitOfWork

    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter

    with _mock_aws():
        uow = DynamoDBUnitOfWork(
            aws_client=None,
            logger=LoggingAdapter("test.dynamo.uow"),
            region="us-east-1",
        )
        yield uow


@pytest.fixture(params=["json", "sql", "dynamodb"])
def unit_of_work(request, json_uow, sql_uow, dynamodb_uow):
    """Parameterised UoW fixture used by UoW contract tests."""
    return {
        "json": json_uow,
        "sql": sql_uow,
        "dynamodb": dynamodb_uow,
    }[request.param]
