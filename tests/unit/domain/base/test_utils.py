"""Tests for orb.domain.base.utils."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from orb.domain.base.utils import parse_iso_datetime


@pytest.mark.unit
class TestParseIsoDatetime:
    @pytest.mark.parametrize("text", ["2026-01-15T12:00:00Z", "2026-01-15T12:00:00z"])
    def test_accepts_z_designator(self, text: str) -> None:
        assert parse_iso_datetime(text) == datetime(2026, 1, 15, 12, tzinfo=timezone.utc)

    def test_accepts_z_with_fractional_seconds(self) -> None:
        parsed = parse_iso_datetime("2026-01-15T12:00:00.123456Z")
        assert parsed == datetime(2026, 1, 15, 12, 0, 0, 123456, tzinfo=timezone.utc)

    def test_keeps_explicit_offset(self) -> None:
        parsed = parse_iso_datetime("2026-01-15T12:00:00+02:00")
        assert parsed.utcoffset() == timedelta(hours=2)

    def test_naive_value_is_taken_as_utc(self) -> None:
        assert parse_iso_datetime("2026-01-15T12:00:00").tzinfo == timezone.utc

    @pytest.mark.parametrize("text", ["", "Z", "not-a-date", "2026-13-45T00:00:00Z"])
    def test_rejects_invalid_values(self, text: str) -> None:
        with pytest.raises(ValueError):
            parse_iso_datetime(text)
