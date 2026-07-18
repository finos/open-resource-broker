"""Unit tests for storage layer exceptions (exceptions.py)."""

import pytest

from orb.infrastructure.storage.exceptions import (
    ConnectionError,
    DatabaseError,
    DataIntegrityError,
    StorageError,
    TransactionError,
)

# ---------------------------------------------------------------------------
# StorageError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStorageError:
    def test_basic_construction(self) -> None:
        err = StorageError("something failed")
        # StorageError calls super().__init__("Storage", message) so error_code holds the msg
        assert err.error_code == "something failed"
        assert err.cause is None

    def test_with_cause(self) -> None:
        cause = ValueError("root cause")
        err = StorageError("wrapper", cause=cause)
        assert err.cause is cause

    def test_to_dict_without_cause(self) -> None:
        err = StorageError("no cause")
        d = err.to_dict()
        assert isinstance(d, dict)
        assert "cause" not in d

    def test_to_dict_with_cause(self) -> None:
        cause = RuntimeError("exploded")
        err = StorageError("with cause", cause=cause)
        d = err.to_dict()
        assert "cause" in d
        assert "exploded" in d["cause"]


# ---------------------------------------------------------------------------
# ConnectionError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConnectionError:
    def test_basic_construction(self) -> None:
        err = ConnectionError("conn failed")
        # error_code holds the constructor message string
        assert err.error_code == "conn failed"
        assert err.connection_details == {}

    def test_with_connection_details(self) -> None:
        details = {"host": "db.example.com", "port": 5432}
        err = ConnectionError("fail", connection_details=details)
        assert err.connection_details == details

    def test_to_dict_includes_safe_connection_details(self) -> None:
        details = {"host": "db.example.com", "password": "secret123", "port": 5432}
        err = ConnectionError("fail", connection_details=details)
        d = err.to_dict()
        assert "connection_details" in d
        assert d["connection_details"]["password"] == "***"
        assert d["connection_details"]["host"] == "db.example.com"

    def test_to_dict_redacts_secret_and_key(self) -> None:
        details = {"secret": "s3cr3t", "key": "k3y", "token": "t0k3n"}
        err = ConnectionError("fail", connection_details=details)
        d = err.to_dict()
        cd = d["connection_details"]
        assert cd["secret"] == "***"
        assert cd["key"] == "***"
        assert cd["token"] == "***"

    def test_to_dict_empty_details_not_included(self) -> None:
        err = ConnectionError("no details")
        d = err.to_dict()
        assert "connection_details" not in d


# ---------------------------------------------------------------------------
# DataIntegrityError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDataIntegrityError:
    def test_basic_construction(self) -> None:
        err = DataIntegrityError("integrity fail")
        assert err.entity_type is None
        assert err.entity_id is None

    def test_with_entity_info(self) -> None:
        err = DataIntegrityError("dup key", entity_type="Machine", entity_id="m-001")
        assert err.entity_type == "Machine"
        assert err.entity_id == "m-001"

    def test_to_dict_includes_entity_type(self) -> None:
        err = DataIntegrityError("msg", entity_type="Request")
        d = err.to_dict()
        assert d["entity_type"] == "Request"

    def test_to_dict_includes_entity_id(self) -> None:
        err = DataIntegrityError("msg", entity_id="req-123")
        d = err.to_dict()
        assert d["entity_id"] == "req-123"

    def test_to_dict_no_entity_omits_keys(self) -> None:
        err = DataIntegrityError("plain")
        d = err.to_dict()
        assert "entity_type" not in d
        assert "entity_id" not in d


# ---------------------------------------------------------------------------
# DatabaseError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDatabaseError:
    def test_basic_construction(self) -> None:
        err = DatabaseError("db error")
        assert err.storage_type is None

    def test_with_storage_type(self) -> None:
        err = DatabaseError("crash", storage_type="postgresql")
        assert err.storage_type == "postgresql"

    def test_to_dict_includes_storage_type(self) -> None:
        err = DatabaseError("crash", storage_type="sqlite")
        d = err.to_dict()
        assert d["storage_type"] == "sqlite"

    def test_to_dict_omits_storage_type_when_none(self) -> None:
        err = DatabaseError("plain")
        d = err.to_dict()
        assert "storage_type" not in d


# ---------------------------------------------------------------------------
# TransactionError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTransactionError:
    def test_basic_construction(self) -> None:
        err = TransactionError("tx failed")
        assert err.transaction_id is None

    def test_with_transaction_id(self) -> None:
        err = TransactionError("fail", transaction_id="txn-abc")
        assert err.transaction_id == "txn-abc"

    def test_to_dict_includes_transaction_id(self) -> None:
        err = TransactionError("fail", transaction_id="txn-xyz")
        d = err.to_dict()
        assert d["transaction_id"] == "txn-xyz"

    def test_to_dict_omits_transaction_id_when_none(self) -> None:
        err = TransactionError("plain")
        d = err.to_dict()
        assert "transaction_id" not in d


# ---------------------------------------------------------------------------
# Hierarchy check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExceptionHierarchy:
    def test_connection_error_is_storage_error(self) -> None:
        assert issubclass(ConnectionError, StorageError)

    def test_data_integrity_error_is_storage_error(self) -> None:
        assert issubclass(DataIntegrityError, StorageError)

    def test_database_error_is_storage_error(self) -> None:
        assert issubclass(DatabaseError, StorageError)

    def test_transaction_error_is_storage_error(self) -> None:
        assert issubclass(TransactionError, StorageError)
