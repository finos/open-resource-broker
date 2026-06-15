"""Unit tests for DynamoDBConverter type conversion.

Direct coverage for two type-handling edge cases on the create/read
round-trip:

* booleans must serialize as DynamoDB BOOL, not Decimal (bool is a
  subclass of int, so the bool check must precede the numeric check)
* ISO-8601 timestamps must be returned as strings on read, not parsed
  to datetime (the domain/repository layer owns datetime parsing)

These are unit tests against the converter only (no AWS / moto needed).
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from orb.providers.aws.storage.components.dynamodb_converter import DynamoDBConverter


@pytest.fixture
def converter() -> DynamoDBConverter:
    return DynamoDBConverter(partition_key="id")


# --- booleans must not be coerced to Decimal --------------------------------


def test_bool_true_serialized_as_bool(converter):
    assert converter._convert_to_dynamodb_type(True) is True


def test_bool_false_serialized_as_bool(converter):
    assert converter._convert_to_dynamodb_type(False) is False


def test_int_serialized_as_decimal(converter):
    result = converter._convert_to_dynamodb_type(5)
    assert result == Decimal("5")
    assert isinstance(result, Decimal)


def test_float_serialized_as_decimal(converter):
    result = converter._convert_to_dynamodb_type(1.5)
    assert result == Decimal("1.5")
    assert isinstance(result, Decimal)


# --- ISO strings returned as-is on read (no eager datetime) -----------------


def test_iso_timestamp_returned_as_str_on_read(converter):
    value = converter._convert_from_dynamodb_type("2026-06-11T10:30:45+00:00")
    assert isinstance(value, str)
    assert value == "2026-06-11T10:30:45+00:00"


def test_zulu_timestamp_returned_as_str_on_read(converter):
    value = converter._convert_from_dynamodb_type("2026-06-11T10:30:45Z")
    assert isinstance(value, str)


def test_plain_string_returned_as_str_on_read(converter):
    assert converter._convert_from_dynamodb_type("RunInstances") == "RunInstances"


# --- write path still serialises datetime objects to ISO strings ------------


def test_datetime_object_serialized_to_iso_on_write(converter):
    dt = datetime(2026, 6, 11, 10, 30, 45, tzinfo=timezone.utc)
    assert converter._convert_to_dynamodb_type(dt) == dt.isoformat()


# --- full request-shaped round-trip integrity -------------------------------


def test_request_item_roundtrip_preserves_types(converter):
    """A request-shaped item survives to_dynamodb_item -> from_dynamodb_item
    with bool, numeric and timestamp types intact."""
    item = converter.to_dynamodb_item(
        "req-1",
        {
            "id": "req-1",
            "dry_run": True,
            "requested_count": 2,
            "provider_api": "RunInstances",
            "created_at": "2026-06-11T10:30:45+00:00",
        },
    )

    # On-the-wire form: bool stays BOOL, number becomes Decimal.
    assert item["dry_run"] is True
    assert item["requested_count"] == Decimal("2")

    back = converter.from_dynamodb_item(item)

    assert back["dry_run"] is True
    assert back["requested_count"] == Decimal("2")
    assert back["provider_api"] == "RunInstances"
    # Timestamp comes back as a string for the domain layer to parse.
    assert isinstance(back["created_at"], str)
    assert back["created_at"] == "2026-06-11T10:30:45+00:00"


def test_nested_bool_in_dict_roundtrip(converter):
    """Booleans nested inside a map field are also preserved."""
    item = converter.to_dynamodb_item(
        "req-2", {"id": "req-2", "metadata": {"dry_run": True, "retries": 3}}
    )
    back = converter.from_dynamodb_item(item)
    assert back["metadata"]["dry_run"] is True
    assert back["metadata"]["retries"] == Decimal("3")
