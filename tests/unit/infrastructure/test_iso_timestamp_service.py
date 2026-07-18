"""Unit tests for services/iso_timestamp_service.py."""

from datetime import datetime, timedelta, timezone

import pytest

from orb.infrastructure.services.iso_timestamp_service import ISOTimestampService


@pytest.mark.unit
class TestISOTimestampServiceFormatForDisplay:
    """Tests for format_for_display."""

    def setup_method(self) -> None:
        self.svc = ISOTimestampService()

    def test_none_returns_none(self) -> None:
        assert self.svc.format_for_display(None) is None

    def test_unix_int_formatted(self) -> None:
        ts = 1700000000
        result = self.svc.format_for_display(ts)
        assert result is not None
        assert result.endswith("Z")
        # Must be a valid ISO-like string
        datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ")

    def test_unix_float_formatted(self) -> None:
        result = self.svc.format_for_display(1700000000.5)
        assert result is not None
        assert "T" in result and result.endswith("Z")

    def test_datetime_with_utc_tz_formatted(self) -> None:
        dt = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = self.svc.format_for_display(dt)
        assert result == "2024-06-01T12:00:00Z"

    def test_naive_datetime_treated_as_utc(self) -> None:
        dt = datetime(2024, 6, 1, 12, 0, 0)
        result = self.svc.format_for_display(dt)
        assert result == "2024-06-01T12:00:00Z"

    def test_datetime_with_non_utc_tz_converted_to_utc(self) -> None:
        tz_plus2 = timezone(timedelta(hours=2))
        dt = datetime(2024, 6, 1, 14, 0, 0, tzinfo=tz_plus2)
        result = self.svc.format_for_display(dt)
        # 14:00 +02 == 12:00 UTC
        assert result == "2024-06-01T12:00:00Z"

    def test_unknown_type_returns_none(self) -> None:
        result = self.svc.format_for_display("2024-01-01")  # type: ignore[arg-type]
        assert result is None


@pytest.mark.unit
class TestISOTimestampServiceFormatForDto:
    """Tests for format_for_dto."""

    def setup_method(self) -> None:
        self.svc = ISOTimestampService()

    def test_none_returns_none(self) -> None:
        assert self.svc.format_for_dto(None) is None

    def test_int_returned_as_is(self) -> None:
        assert self.svc.format_for_dto(1700000000) == 1700000000

    def test_float_truncated_to_int(self) -> None:
        result = self.svc.format_for_dto(1700000000.9)
        assert isinstance(result, int)
        assert result == 1700000000

    def test_datetime_returns_unix_timestamp(self) -> None:
        dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = self.svc.format_for_dto(dt)
        assert isinstance(result, int)
        assert result == int(dt.timestamp())

    def test_unknown_type_returns_none(self) -> None:
        result = self.svc.format_for_dto("not a timestamp")  # type: ignore[arg-type]
        assert result is None


@pytest.mark.unit
class TestISOTimestampServiceCurrentTimestamp:
    """Tests for current_timestamp."""

    def test_returns_string(self) -> None:
        svc = ISOTimestampService()
        result = svc.current_timestamp()
        assert isinstance(result, str)

    def test_returns_utc_iso_format(self) -> None:
        svc = ISOTimestampService()
        result = svc.current_timestamp()
        assert result.endswith("Z")
        datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.unit
class TestISOTimestampServiceFormatWithType:
    """Tests for format_with_type."""

    def setup_method(self) -> None:
        self.svc = ISOTimestampService()

    def test_unix_format_type(self) -> None:
        result = self.svc.format_with_type(1700000000, "unix")
        assert isinstance(result, int)
        assert result == 1700000000

    def test_iso_format_type(self) -> None:
        result = self.svc.format_with_type(1700000000, "iso")
        assert isinstance(result, str)
        assert result is not None
        assert result.endswith("Z")  # type: ignore[union-attr]

    def test_auto_format_type_returns_int(self) -> None:
        result = self.svc.format_with_type(1700000000, "auto")
        assert isinstance(result, int)

    def test_unknown_format_type_returns_int(self) -> None:
        result = self.svc.format_with_type(1700000000, "csv")
        assert isinstance(result, int)

    def test_none_input_returns_none(self) -> None:
        assert self.svc.format_with_type(None, "iso") is None
        assert self.svc.format_with_type(None, "unix") is None
