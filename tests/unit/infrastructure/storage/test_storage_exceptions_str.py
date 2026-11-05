"""String form of storage exceptions."""

from orb.infrastructure.storage.exceptions import StorageError, TransactionError


def test_transaction_error_str_carries_the_detail():
    err = TransactionError("DynamoDB transaction failed: call 2 of 3; 1 earlier call(s) applied")

    assert "call 2 of 3" in str(err)
    assert "call 2 of 3" in err.message


def test_storage_errors_show_their_detail():
    err = StorageError("detail")

    assert str(err) == "detail"
    assert err.message == "detail"
    assert err.error_code == "StorageError"
