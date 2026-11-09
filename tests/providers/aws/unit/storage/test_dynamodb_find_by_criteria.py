"""DynamoDB find_by_criteria: scan failures raise instead of looking like no matches.

Runs against moto.
"""

from __future__ import annotations

import pytest

moto = pytest.importorskip("moto")

from botocore.exceptions import ClientError  # noqa: E402

from orb.infrastructure.adapters.logging_adapter import LoggingAdapter  # noqa: E402
from orb.infrastructure.storage.exceptions import StorageError  # noqa: E402
from orb.providers.aws.storage.strategy import DynamoDBStorageStrategy  # noqa: E402


@pytest.fixture
def strategy():
    with moto.mock_aws():
        yield DynamoDBStorageStrategy(
            logger=LoggingAdapter("test.dynamo.find_by_criteria"),
            aws_client=None,
            region="us-east-1",
            table_name="machines-find-by-criteria",
        )


def test_missing_table_raises_storage_error(strategy) -> None:
    # The constructor creates the table, so remove it to simulate a missing table.
    strategy.client_manager.dynamodb.delete_table(TableName=strategy.table_name)

    with pytest.raises(StorageError) as exc_info:
        strategy.find_by_criteria({"status": "running"})

    cause = exc_info.value.__cause__
    assert isinstance(cause, ClientError)
    assert cause.response["Error"]["Code"] == "ResourceNotFoundException"


def test_matching_scan_returns_rows(strategy) -> None:
    strategy.save("m-1", {"id": "m-1", "status": "running"})
    strategy.save("m-2", {"id": "m-2", "status": "stopped"})

    result = strategy.find_by_criteria({"status": "running"})

    assert [row["id"] for row in result] == ["m-1"]
