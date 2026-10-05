"""Unit tests for DynamoDBClientManager.

Covers client lifecycle (init/cleanup), health checks, table CRUD helpers,
error classification, and client-error handling using moto-mocked DynamoDB
where real AWS semantics matter (table_exists, create_table, item CRUD,
scan pagination) and plain mocks for failure-injection paths that moto
cannot easily reproduce.
"""

from unittest import mock

import pytest
from botocore.exceptions import ClientError

try:
    from moto import mock_aws

    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from orb.providers.aws.storage.components.dynamodb_client_manager import (
    DynamoDBClientManager,
)

pytestmark = pytest.mark.skipif(not HAS_MOTO, reason="moto not installed")


_KEY_SCHEMA = [{"AttributeName": "id", "KeyType": "HASH"}]
_ATTRIBUTE_DEFINITIONS = [{"AttributeName": "id", "AttributeType": "S"}]


@pytest.fixture
def manager():
    """A DynamoDBClientManager with real moto-backed boto3 clients."""
    with mock_aws():
        yield DynamoDBClientManager(aws_client=None, region="us-east-1")


class TestInitialization:
    """Validate client construction and (re-)initialization."""

    def test_initializes_clients_when_no_aws_client_given(self, manager) -> None:
        """Without an injected aws_client, boto3 clients are created directly."""
        assert manager.dynamodb is not None
        assert manager.dynamodb_resource is not None
        assert manager._initialized is True
        assert manager.aws_client is None

    def test_uses_injected_aws_client(self) -> None:
        """When an aws_client wrapper is given, its clients are reused as-is."""
        fake_client = mock.Mock()
        fake_client.dynamodb_client = mock.Mock(name="dynamo-client")
        fake_client.dynamodb_resource = mock.Mock(name="dynamo-resource")

        manager = DynamoDBClientManager(aws_client=fake_client, region="us-east-1")

        assert manager.aws_client is fake_client
        assert manager.dynamodb is fake_client.dynamodb_client
        assert manager.dynamodb_resource is fake_client.dynamodb_resource
        assert manager._initialized is True

    def test_initialize_is_a_noop_once_initialized(self, manager) -> None:
        """Calling initialize() again does not recreate the clients."""
        original_client = manager.dynamodb
        manager.initialize()
        assert manager.dynamodb is original_client

    def test_initialize_clients_failure_is_logged_and_reraised(self) -> None:
        """A session creation failure propagates after being logged."""
        with (
            mock.patch(
                "orb.providers.aws.session_factory.AWSSessionFactory.create_session",
                side_effect=RuntimeError("no credentials"),
            ),
            pytest.raises(RuntimeError, match="no credentials"),
        ):
            DynamoDBClientManager(aws_client=None, region="us-east-1")


class TestCleanup:
    """Validate cleanup() resets all client state."""

    def test_cleanup_resets_state(self, manager) -> None:
        """After cleanup, all client references and the initialized flag are cleared."""
        manager.cleanup()
        assert manager.dynamodb is None
        assert manager.dynamodb_resource is None
        assert manager.aws_client is None
        assert manager._initialized is False


class TestIsHealthy:
    """Validate is_healthy() across success/failure/uninitialized paths."""

    def test_healthy_when_list_tables_succeeds(self, manager) -> None:
        """A working DynamoDB client reports healthy."""
        assert manager.is_healthy() is True

    def test_unhealthy_when_no_client(self, manager) -> None:
        """Without a dynamodb client, health check fails fast."""
        manager.dynamodb = None
        assert manager.is_healthy() is False

    def test_unhealthy_when_list_tables_raises(self, manager) -> None:
        """An exception from list_tables is logged and reported as unhealthy."""
        manager.dynamodb = mock.Mock()
        manager.dynamodb.list_tables.side_effect = RuntimeError("boom")
        assert manager.is_healthy() is False


class TestGetConnectionInfo:
    """Validate get_connection_info() summary dict."""

    def test_reports_initialized_and_healthy_state(self, manager) -> None:
        """Connection info reflects region, profile, and health when initialized."""
        info = manager.get_connection_info()
        assert info["type"] == "dynamodb"
        assert info["region"] == "us-east-1"
        assert info["initialized"] is True
        assert info["healthy"] is True
        assert info["has_dynamodb_client"] is True
        assert info["has_dynamodb_resource"] is True

    def test_not_healthy_when_not_initialized(self, manager) -> None:
        """Health is never checked (and reported False) when uninitialized."""
        manager.cleanup()
        info = manager.get_connection_info()
        assert info["initialized"] is False
        assert info["healthy"] is False


class TestTableLifecycle:
    """Validate table_exists / create_table / get_table against moto."""

    def test_table_does_not_exist_initially(self, manager) -> None:
        """A table that was never created does not exist."""
        assert manager.table_exists("missing-table") is False

    def test_create_table_then_exists(self, manager) -> None:
        """A newly created table is reported as existing."""
        created = manager.create_table("my-table", _KEY_SCHEMA, _ATTRIBUTE_DEFINITIONS)
        assert created is True
        assert manager.table_exists("my-table") is True

    def test_create_table_is_idempotent(self, manager) -> None:
        """Creating an already-existing table short-circuits successfully."""
        manager.create_table("my-table", _KEY_SCHEMA, _ATTRIBUTE_DEFINITIONS)
        created_again = manager.create_table("my-table", _KEY_SCHEMA, _ATTRIBUTE_DEFINITIONS)
        assert created_again is True

    def test_create_table_provisioned_billing_mode(self, manager) -> None:
        """PROVISIONED billing mode attaches explicit throughput settings."""
        created = manager.create_table(
            "provisioned-table",
            _KEY_SCHEMA,
            _ATTRIBUTE_DEFINITIONS,
            billing_mode="PROVISIONED",
        )
        assert created is True
        assert manager.table_exists("provisioned-table") is True

    def test_create_table_client_error_returns_false(self, manager) -> None:
        """A ClientError during table creation is caught and reported as failure."""
        manager.dynamodb.create_table = mock.Mock(
            side_effect=ClientError(
                {"Error": {"Code": "LimitExceededException", "Message": "nope"}},
                "CreateTable",
            )
        )
        created = manager.create_table("another-table", _KEY_SCHEMA, _ATTRIBUTE_DEFINITIONS)
        assert created is False

    def test_table_exists_resource_not_found_returns_false(self, manager) -> None:
        """ResourceNotFoundException from describe_table means the table is absent."""
        manager.dynamodb.describe_table = mock.Mock(
            side_effect=ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "n/a"}},
                "DescribeTable",
            )
        )
        assert manager.table_exists("whatever") is False

    def test_table_exists_other_client_error_reraises(self, manager) -> None:
        """Non-ResourceNotFoundException ClientErrors propagate."""
        manager.dynamodb.describe_table = mock.Mock(
            side_effect=ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "nope"}},
                "DescribeTable",
            )
        )
        with pytest.raises(ClientError):
            manager.table_exists("whatever")

    def test_table_exists_unexpected_exception_returns_false(self, manager) -> None:
        """Non-ClientError exceptions are swallowed and reported as False."""
        manager.dynamodb.describe_table = mock.Mock(side_effect=RuntimeError("boom"))
        assert manager.table_exists("whatever") is False

    def test_get_table_failure_is_logged_and_reraised(self, manager) -> None:
        """A failure resolving the table resource propagates after logging."""
        manager.dynamodb_resource.Table = mock.Mock(side_effect=RuntimeError("bad"))
        with pytest.raises(RuntimeError, match="bad"):
            manager.get_table("my-table")


class TestItemOperations:
    """Validate put_item / get_item / delete_item against a real moto table."""

    @pytest.fixture(autouse=True)
    def _table(self, manager):
        manager.create_table("items-table", _KEY_SCHEMA, _ATTRIBUTE_DEFINITIONS)
        self.manager = manager

    def test_put_and_get_item_round_trip(self) -> None:
        """An item written with put_item can be read back with get_item."""
        assert self.manager.put_item("items-table", {"id": "1", "name": "a"}) is True
        item = self.manager.get_item("items-table", {"id": "1"})
        assert item == {"id": "1", "name": "a"}

    def test_get_item_missing_returns_none(self) -> None:
        """Reading a key that was never written returns None."""
        assert self.manager.get_item("items-table", {"id": "missing"}) is None

    def test_delete_item_removes_it(self) -> None:
        """A deleted item is no longer retrievable."""
        self.manager.put_item("items-table", {"id": "2", "name": "b"})
        assert self.manager.delete_item("items-table", {"id": "2"}) is True
        assert self.manager.get_item("items-table", {"id": "2"}) is None

    def test_put_item_failure_returns_false(self) -> None:
        """An exception during put_item is caught and reported as failure."""
        with mock.patch.object(self.manager, "get_table", side_effect=RuntimeError("boom")):
            assert self.manager.put_item("items-table", {"id": "3"}) is False

    def test_get_item_failure_returns_none(self) -> None:
        """An exception during get_item is caught and reported as None."""
        with mock.patch.object(self.manager, "get_table", side_effect=RuntimeError("boom")):
            assert self.manager.get_item("items-table", {"id": "3"}) is None

    def test_delete_item_failure_returns_false(self) -> None:
        """An exception during delete_item is caught and reported as failure."""
        with mock.patch.object(self.manager, "get_table", side_effect=RuntimeError("boom")):
            assert self.manager.delete_item("items-table", {"id": "3"}) is False


class TestScanTable:
    """Validate scan_table, including pagination and failure handling."""

    def test_scan_returns_all_items(self, manager) -> None:
        """All items written to the table are returned by a scan."""
        manager.create_table("scan-table", _KEY_SCHEMA, _ATTRIBUTE_DEFINITIONS)
        for i in range(3):
            manager.put_item("scan-table", {"id": str(i)})
        items = manager.scan_table("scan-table")
        assert {item["id"] for item in items} == {"0", "1", "2"}

    def test_scan_follows_pagination(self, manager) -> None:
        """A paginated response (LastEvaluatedKey) triggers a follow-up scan."""
        page_one = {"Items": [{"id": "1"}], "LastEvaluatedKey": {"id": "1"}}
        page_two = {"Items": [{"id": "2"}]}
        fake_table = mock.Mock()
        fake_table.scan = mock.Mock(side_effect=[page_one, page_two])
        with mock.patch.object(manager, "get_table", return_value=fake_table):
            items = manager.scan_table("scan-table")
        assert items == [{"id": "1"}, {"id": "2"}]
        assert fake_table.scan.call_count == 2

    def test_scan_with_filter_expression_and_values(self, manager) -> None:
        """Filter expression and attribute values are forwarded to scan()."""
        fake_table = mock.Mock()
        fake_table.scan = mock.Mock(return_value={"Items": []})
        with mock.patch.object(manager, "get_table", return_value=fake_table):
            manager.scan_table(
                "scan-table",
                filter_expression="attr",
                expression_attribute_values={":v": "x"},
            )
        _, kwargs = fake_table.scan.call_args
        assert kwargs["FilterExpression"] == "attr"
        assert kwargs["ExpressionAttributeValues"] == {":v": "x"}

    def test_scan_failure_returns_empty_list(self, manager) -> None:
        """An exception during scan is caught and an empty list is returned."""
        with mock.patch.object(manager, "get_table", side_effect=RuntimeError("boom")):
            assert manager.scan_table("scan-table") == []


class TestBatchWriteItems:
    """Validate batch_write_items against a real moto table."""

    def test_batch_write_succeeds(self, manager) -> None:
        """Multiple items can be written in a single batch_write_items call."""
        manager.create_table("batch-table", _KEY_SCHEMA, _ATTRIBUTE_DEFINITIONS)
        items = [{"id": str(i)} for i in range(5)]
        assert manager.batch_write_items("batch-table", items) is True
        scanned = manager.scan_table("batch-table")
        assert len(scanned) == 5

    def test_batch_write_failure_returns_false(self, manager) -> None:
        """An exception while obtaining the table is caught and reported false."""
        with mock.patch.object(manager, "get_table", side_effect=RuntimeError("boom")):
            assert manager.batch_write_items("batch-table", [{"id": "1"}]) is False


class TestHandleClientError:
    """Validate handle_client_error logs without raising for each error code."""

    @pytest.mark.parametrize(
        "error_code",
        [
            "ResourceNotFoundException",
            "ValidationException",
            "ConditionalCheckFailedException",
            "ProvisionedThroughputExceededException",
            "SomeOtherException",
        ],
    )
    def test_handle_client_error_does_not_raise(self, manager, error_code) -> None:
        """Each known (and unknown) error code is handled without raising."""
        error = ClientError({"Error": {"Code": error_code, "Message": "details"}}, "PutItem")
        manager.handle_client_error(error, "PutItem")


class TestAccessors:
    """Validate get_client / get_resource accessors."""

    def test_get_client_returns_dynamodb_client(self, manager) -> None:
        """get_client() returns the underlying boto3 DynamoDB client."""
        assert manager.get_client() is manager.dynamodb

    def test_get_resource_returns_dynamodb_resource(self, manager) -> None:
        """get_resource() returns the underlying boto3 DynamoDB resource."""
        assert manager.get_resource() is manager.dynamodb_resource
