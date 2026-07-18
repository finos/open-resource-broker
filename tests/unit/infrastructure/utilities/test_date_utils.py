"""Tests for common/date_utils.py utilities."""

import datetime

import pytest

from orb.infrastructure.utilities.common.date_utils import (
    add_days,
    add_hours,
    add_minutes,
    add_seconds,
    datetime_to_timestamp,
    format_datetime,
    format_timestamp,
    get_current_date,
    get_current_datetime,
    get_current_timestamp,
    get_current_timestamp_ms,
    get_date_range,
    get_datetime_range,
    get_day_name,
    get_day_of_week,
    get_days_in_month,
    get_days_in_year,
    get_end_of_day,
    get_end_of_month,
    get_end_of_quarter,
    get_end_of_year,
    get_month_name,
    get_quarter,
    get_start_of_day,
    get_start_of_month,
    get_start_of_quarter,
    get_start_of_year,
    get_time_difference,
    get_time_difference_days,
    get_time_difference_hours,
    get_time_difference_minutes,
    get_time_difference_seconds,
    get_week_number,
    is_leap_year,
    is_same_day,
    is_same_month,
    is_same_year,
    parse_date,
    parse_datetime,
    timestamp_to_datetime,
)

# Fixed reference datetime for deterministic tests
REF_DT = datetime.datetime(2024, 3, 15, 10, 30, 0, tzinfo=datetime.timezone.utc)


@pytest.mark.unit
class TestCurrentTimeHelpers:
    """Tests for current timestamp/datetime functions."""

    def test_get_current_timestamp_returns_positive_float(self):
        ts = get_current_timestamp()
        assert isinstance(ts, float)
        assert ts > 0

    def test_get_current_timestamp_ms_returns_positive_int(self):
        ms = get_current_timestamp_ms()
        assert isinstance(ms, int)
        assert ms > 0

    def test_get_current_datetime_returns_utc_aware(self):
        dt = get_current_datetime()
        assert isinstance(dt, datetime.datetime)
        assert dt.tzinfo is not None

    def test_get_current_date_returns_date_type(self):
        d = get_current_date()
        assert isinstance(d, datetime.date)


@pytest.mark.unit
class TestFormatAndParse:
    """Tests for format/parse functions."""

    def test_format_timestamp_default_format(self):
        ts = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
        result = format_timestamp(ts)
        assert "2024-01-15" in result

    def test_format_timestamp_custom_format(self):
        ts = datetime.datetime(2024, 6, 1, 0, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
        result = format_timestamp(ts, "%Y/%m/%d")
        assert result == "2024/06/01"

    def test_format_datetime_default(self):
        dt = datetime.datetime(2024, 3, 15, 10, 30, 0)
        assert format_datetime(dt) == "2024-03-15 10:30:00"

    def test_format_datetime_custom_format(self):
        dt = datetime.datetime(2024, 3, 15, 10, 30, 0)
        assert format_datetime(dt, "%d/%m/%Y") == "15/03/2024"

    def test_parse_datetime_default_format(self):
        result = parse_datetime("2024-03-15 10:30:00")
        assert result == datetime.datetime(2024, 3, 15, 10, 30, 0)

    def test_parse_datetime_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_datetime("not-a-date")

    def test_parse_date_default_format(self):
        result = parse_date("2024-03-15")
        assert result == datetime.date(2024, 3, 15)

    def test_parse_date_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_date("bad-date")


@pytest.mark.unit
class TestTimestampConversion:
    """Tests for datetime_to_timestamp and timestamp_to_datetime."""

    def test_datetime_to_timestamp_and_back(self):
        ts = datetime_to_timestamp(REF_DT)
        result = timestamp_to_datetime(ts)
        assert result.year == REF_DT.year
        assert result.month == REF_DT.month
        assert result.day == REF_DT.day

    def test_timestamp_to_datetime_returns_utc_aware(self):
        result = timestamp_to_datetime(0.0)
        assert result.tzinfo == datetime.timezone.utc
        assert result.year == 1970


@pytest.mark.unit
class TestAddTimedelta:
    """Tests for add_days, add_hours, add_minutes, add_seconds."""

    def test_add_days_positive(self):
        result = add_days(REF_DT, 5)
        assert result.day == 20

    def test_add_days_negative(self):
        result = add_days(REF_DT, -14)
        assert result.day == 1

    def test_add_hours(self):
        result = add_hours(REF_DT, 3)
        assert result.hour == 13

    def test_add_minutes(self):
        result = add_minutes(REF_DT, 45)
        assert result.minute == 15
        assert result.hour == 11

    def test_add_seconds(self):
        result = add_seconds(REF_DT, 90)
        assert result.second == 30
        assert result.minute == 31


@pytest.mark.unit
class TestTimeDifference:
    """Tests for time difference utilities."""

    def test_get_time_difference_returns_timedelta(self):
        dt1 = REF_DT
        dt2 = REF_DT - datetime.timedelta(hours=2)
        diff = get_time_difference(dt1, dt2)
        assert diff.total_seconds() == 7200

    def test_get_time_difference_seconds(self):
        dt1 = REF_DT
        dt2 = REF_DT - datetime.timedelta(seconds=150)
        assert get_time_difference_seconds(dt1, dt2) == 150.0

    def test_get_time_difference_minutes(self):
        dt1 = REF_DT
        dt2 = REF_DT - datetime.timedelta(minutes=3)
        assert get_time_difference_minutes(dt1, dt2) == pytest.approx(3.0)

    def test_get_time_difference_hours(self):
        dt1 = REF_DT
        dt2 = REF_DT - datetime.timedelta(hours=2)
        assert get_time_difference_hours(dt1, dt2) == pytest.approx(2.0)

    def test_get_time_difference_days(self):
        dt1 = REF_DT
        dt2 = REF_DT - datetime.timedelta(days=3)
        assert get_time_difference_days(dt1, dt2) == pytest.approx(3.0)


@pytest.mark.unit
class TestSameDay:
    """Tests for is_same_day, is_same_month, is_same_year."""

    def test_is_same_day_same(self):
        dt1 = datetime.datetime(2024, 3, 15, 8, 0)
        dt2 = datetime.datetime(2024, 3, 15, 23, 59)
        assert is_same_day(dt1, dt2) is True

    def test_is_same_day_different(self):
        dt1 = datetime.datetime(2024, 3, 15)
        dt2 = datetime.datetime(2024, 3, 16)
        assert is_same_day(dt1, dt2) is False

    def test_is_same_month_same(self):
        assert is_same_month(datetime.datetime(2024, 3, 1), datetime.datetime(2024, 3, 31)) is True

    def test_is_same_month_different(self):
        assert is_same_month(datetime.datetime(2024, 3, 1), datetime.datetime(2024, 4, 1)) is False

    def test_is_same_year_same(self):
        assert is_same_year(datetime.datetime(2024, 1, 1), datetime.datetime(2024, 12, 31)) is True

    def test_is_same_year_different(self):
        assert is_same_year(datetime.datetime(2024, 1, 1), datetime.datetime(2025, 1, 1)) is False


@pytest.mark.unit
class TestBoundaryHelpers:
    """Tests for start/end of day, month, year, quarter."""

    def test_get_start_of_day(self):
        result = get_start_of_day(REF_DT)
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0

    def test_get_end_of_day(self):
        result = get_end_of_day(REF_DT)
        assert result.hour == 23
        assert result.minute == 59
        assert result.second == 59
        assert result.microsecond == 999999

    def test_get_start_of_month(self):
        result = get_start_of_month(REF_DT)
        assert result.day == 1
        assert result.hour == 0

    def test_get_end_of_month_march(self):
        result = get_end_of_month(REF_DT)
        assert result.day == 31
        assert result.hour == 23

    def test_get_end_of_month_february_non_leap(self):
        dt = datetime.datetime(2023, 2, 1)
        result = get_end_of_month(dt)
        assert result.day == 28

    def test_get_end_of_month_february_leap(self):
        dt = datetime.datetime(2024, 2, 1)
        result = get_end_of_month(dt)
        assert result.day == 29

    def test_get_start_of_year(self):
        result = get_start_of_year(REF_DT)
        assert result.month == 1
        assert result.day == 1
        assert result.hour == 0

    def test_get_end_of_year(self):
        result = get_end_of_year(REF_DT)
        assert result.month == 12
        assert result.day == 31

    def test_get_quarter_q1(self):
        assert get_quarter(datetime.datetime(2024, 1, 15)) == 1

    def test_get_quarter_q2(self):
        assert get_quarter(datetime.datetime(2024, 4, 1)) == 2

    def test_get_quarter_q3(self):
        assert get_quarter(datetime.datetime(2024, 7, 31)) == 3

    def test_get_quarter_q4(self):
        assert get_quarter(datetime.datetime(2024, 12, 1)) == 4

    def test_get_start_of_quarter_q2(self):
        dt = datetime.datetime(2024, 5, 15)
        result = get_start_of_quarter(dt)
        assert result.month == 4
        assert result.day == 1

    def test_get_end_of_quarter_q1(self):
        dt = datetime.datetime(2024, 1, 15)
        result = get_end_of_quarter(dt)
        assert result.month == 3
        assert result.day == 31


@pytest.mark.unit
class TestDateRange:
    """Tests for get_date_range and get_datetime_range."""

    def test_get_date_range_inclusive(self):
        start = datetime.date(2024, 1, 1)
        end = datetime.date(2024, 1, 3)
        result = get_date_range(start, end)
        assert len(result) == 3
        assert result[0] == start
        assert result[-1] == end

    def test_get_date_range_single_day(self):
        d = datetime.date(2024, 6, 15)
        result = get_date_range(d, d)
        assert result == [d]

    def test_get_datetime_range(self):
        start = datetime.datetime(2024, 1, 1, 0, 0, 0)
        end = datetime.datetime(2024, 1, 1, 3, 0, 0)
        delta = datetime.timedelta(hours=1)
        result = get_datetime_range(start, end, delta)
        assert len(result) == 4
        assert result[0] == start
        assert result[-1] == end


@pytest.mark.unit
class TestCalendarHelpers:
    """Tests for leap year, days in month/year, week helpers."""

    def test_is_leap_year_2024(self):
        assert is_leap_year(2024) is True

    def test_is_leap_year_2023(self):
        assert is_leap_year(2023) is False

    def test_is_leap_year_1900(self):
        # Divisible by 100 but not 400 — not a leap year
        assert is_leap_year(1900) is False

    def test_is_leap_year_2000(self):
        assert is_leap_year(2000) is True

    def test_get_days_in_month_january(self):
        assert get_days_in_month(2024, 1) == 31

    def test_get_days_in_month_april(self):
        assert get_days_in_month(2024, 4) == 30

    def test_get_days_in_month_february_leap(self):
        assert get_days_in_month(2024, 2) == 29

    def test_get_days_in_month_february_non_leap(self):
        assert get_days_in_month(2023, 2) == 28

    def test_get_days_in_month_invalid_raises(self):
        with pytest.raises(ValueError, match="Month must be between 1 and 12"):
            get_days_in_month(2024, 13)

    def test_get_days_in_year_leap(self):
        assert get_days_in_year(2024) == 366

    def test_get_days_in_year_non_leap(self):
        assert get_days_in_year(2023) == 365

    def test_get_week_number_first_week_of_year(self):
        # Jan 1 2024 is a Monday, week 1
        dt = datetime.datetime(2024, 1, 1)
        assert get_week_number(dt) == 1

    def test_get_day_of_week_monday(self):
        dt = datetime.datetime(2024, 1, 1)  # Monday
        assert get_day_of_week(dt) == 1

    def test_get_day_of_week_sunday(self):
        dt = datetime.datetime(2024, 1, 7)  # Sunday
        assert get_day_of_week(dt) == 7

    def test_get_day_name_full(self):
        dt = datetime.datetime(2024, 1, 1)  # Monday
        assert get_day_name(dt) == "Monday"

    def test_get_day_name_short(self):
        dt = datetime.datetime(2024, 1, 1)  # Monday
        assert get_day_name(dt, short=True) == "Mon"

    def test_get_month_name_full(self):
        dt = datetime.datetime(2024, 3, 1)
        assert get_month_name(dt) == "March"

    def test_get_month_name_short(self):
        dt = datetime.datetime(2024, 3, 1)
        assert get_month_name(dt, short=True) == "Mar"
