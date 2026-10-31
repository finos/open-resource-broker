"""String form of storage exceptions."""

from orb.infrastructure.storage.exceptions import StorageError, TransactionError


def test_transaction_error_str_carries_the_detail():
    err = TransactionError("DynamoDB transaction failed: call 2 of 3; 1 earlier call(s) applied")

    assert "call 2 of 3" in str(err)
    assert err.message == "Storage"


def test_other_storage_errors_keep_their_string_form():
    assert str(StorageError("detail")) == "Storage"
