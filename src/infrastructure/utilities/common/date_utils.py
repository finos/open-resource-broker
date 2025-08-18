"""
Date utility functions for the AWS Host Factory Plugin.

This module contains utility functions for working with dates and times.
"""

import datetime
import time
from typing import List


def get_current_timestamp() -> float:
    """
    Get current timestamp in seconds since epoch.

    Returns:
        Current timestamp in seconds
    """
    return time.time()


def get_current_timestamp_ms() -> int:
    """
    Get current timestamp in milliseconds since epoch.

    Returns:
        Current timestamp in milliseconds
    """
    return int(time.time() * 1000)


def get_current_datetime() -> datetime.datetime:
    """
    Get current datetime in UTC.

    Returns:
        Current datetime in UTC
    """
    return datetime.datetime.now(datetime.timezone.utc)


def get_current_date() -> datetime.date:
    """
    Get current date in UTC.

    Returns:
        Current date in UTC
    """
    return datetime.datetime.now(datetime.timezone.utc).date()


def format_timestamp(timestamp: float, format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Format a timestamp as a string.

    Args:
        timestamp: Timestamp in seconds since epoch
        format_str: Format string

    Returns:
        Formatted timestamp
    """
    dt = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
    return dt.strftime(format_str)


def format_datetime(dt: datetime.datetime, format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Format a datetime as a string.

    Args:
        dt: Datetime to format
        format_str: Format string

    Returns:
        Formatted datetime
    """
    return dt.strftime(format_str)


def parse_datetime(date_str: str, format_str: str = "%Y-%m-%d %H:%M:%S") -> datetime.datetime:
    """
    Parse a string as a datetime.

    Args:
        date_str: String to parse
        format_str: Format string

    Returns:
        Parsed datetime

    Raises:
        ValueError: If the string cannot be parsed
    """
    return datetime.datetime.strptime(date_str, format_str)


def parse_date(date_str: str, format_str: str = "%Y-%m-%d") -> datetime.date:
    """
    Parse a string as a date.

    Args:
        date_str: String to parse
        format_str: Format string

    Returns:
        Parsed date

    Raises:
        ValueError: If the string cannot be parsed
    """
    return datetime.datetime.strptime(date_str, format_str).date()


def datetime_to_timestamp(dt: datetime.datetime) -> float:
    """
    Convert a datetime to a timestamp.

    Args:
        dt: Datetime to convert

    Returns:
        Timestamp in seconds since epoch
    """
    return dt.timestamp()


def timestamp_to_datetime(timestamp: float) -> datetime.datetime:
    """
    Convert a timestamp to a datetime.

    Args:
        timestamp: Timestamp in seconds since epoch

    Returns:
        Datetime in UTC
    """
    return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)


def add_days(dt: datetime.datetime, days: int) -> datetime.datetime:
    """
    Add days to a datetime.

    Args:
        dt: Datetime to add days to
        days: Number of days to add

    Returns:
        New datetime
    """
    return dt + datetime.timedelta(days=days)


def add_hours(dt: datetime.datetime, hours: int) -> datetime.datetime:
    """
    Add hours to a datetime.

    Args:
        dt: Datetime to add hours to
        hours: Number of hours to add

    Returns:
        New datetime
    """
    return dt + datetime.timedelta(hours=hours)


def add_minutes(dt: datetime.datetime, minutes: int) -> datetime.datetime:
    """
    Add minutes to a datetime.

    Args:
        dt: Datetime to add minutes to
        minutes: Number of minutes to add

    Returns:
        New datetime
    """
    return dt + datetime.timedelta(minutes=minutes)


def add_seconds(dt: datetime.datetime, seconds: int) -> datetime.datetime:
    """
    Add seconds to a datetime.

    Args:
        dt: Datetime to add seconds to
        seconds: Number of seconds to add

    Returns:
        New datetime
    """
    return dt + datetime.timedelta(seconds=seconds)


def get_time_difference(dt1: datetime.datetime, dt2: datetime.datetime) -> datetime.timedelta:
    """
    Get the time difference between two datetimes.

    Args:
        dt1: First datetime
        dt2: Second datetime

    Returns:
        Time difference
    """
    return dt1 - dt2


def get_time_difference_seconds(dt1: datetime.datetime, dt2: datetime.datetime) -> float:
    """
    Get the time difference between two datetimes in seconds.

    Args:
        dt1: First datetime
        dt2: Second datetime

    Returns:
        Time difference in seconds
    """
    return (dt1 - dt2).total_seconds()


def get_time_difference_minutes(dt1: datetime.datetime, dt2: datetime.datetime) -> float:
    """
    Get the time difference between two datetimes in minutes.

    Args:
        dt1: First datetime
        dt2: Second datetime

    Returns:
        Time difference in minutes
    """
    return (dt1 - dt2).total_seconds() / 60


def get_time_difference_hours(dt1: datetime.datetime, dt2: datetime.datetime) -> float:
    """
    Get the time difference between two datetimes in hours.

    Args:
        dt1: First datetime
        dt2: Second datetime

    Returns:
        Time difference in hours
    """
    return (dt1 - dt2).total_seconds() / 3600


def get_time_difference_days(dt1: datetime.datetime, dt2: datetime.datetime) -> float:
    """
    Get the time difference between two datetimes in days.

    Args:
        dt1: First datetime
        dt2: Second datetime

    Returns:
        Time difference in days
    """
    return (dt1 - dt2).total_seconds() / 86400


def is_same_day(dt1: datetime.datetime, dt2: datetime.datetime) -> bool:
    """
    Check if two datetimes are on the same day.

    Args:
        dt1: First datetime
        dt2: Second datetime

    Returns:
        True if the datetimes are on the same day, False otherwise
    """
    return dt1.date() == dt2.date()


def is_same_month(dt1: datetime.datetime, dt2: datetime.datetime) -> bool:
    """
    Check if two datetimes are in the same month.

    Args:
        dt1: First datetime
        dt2: Second datetime

    Returns:
        True if the datetimes are in the same month, False otherwise
    """
    return dt1.year == dt2.year and dt1.month == dt2.month


def is_same_year(dt1: datetime.datetime, dt2: datetime.datetime) -> bool:
    """
    Check if two datetimes are in the same year.

    Args:
        dt1: First datetime
        dt2: Second datetime

    Returns:
        True if the datetimes are in the same year, False otherwise
    """
    return dt1.year == dt2.year


def get_start_of_day(dt: datetime.datetime) -> datetime.datetime:
    """
    Get the start of the day for a datetime.

    Args:
        dt: Datetime

    Returns:
        Datetime at the start of the day
    """
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def get_end_of_day(dt: datetime.datetime) -> datetime.datetime:
    """
    Get the end of the day for a datetime.

    Args:
        dt: Datetime

    Returns:
        Datetime at the end of the day
    """
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999)


def get_start_of_month(dt: datetime.datetime) -> datetime.datetime:
    """
    Get the start of the month for a datetime.

    Args:
        dt: Datetime

    Returns:
        Datetime at the start of the month
    """
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def get_end_of_month(dt: datetime.datetime) -> datetime.datetime:
    """
    Get the end of the month for a datetime.

    Args:
        dt: Datetime

    Returns:
        Datetime at the end of the month
    """
    next_month = dt.replace(day=28) + datetime.timedelta(days=4)
    last_day = next_month.replace(day=1) - datetime.timedelta(days=1)
    return last_day.replace(hour=23, minute=59, second=59, microsecond=999999)


def get_start_of_year(dt: datetime.datetime) -> datetime.datetime:
    """
    Get the start of the year for a datetime.

    Args:
        dt: Datetime

    Returns:
        Datetime at the start of the year
    """
    return dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def get_end_of_year(dt: datetime.datetime) -> datetime.datetime:
    """
    Get the end of the year for a datetime.

    Args:
        dt: Datetime

    Returns:
        Datetime at the end of the year
    """
    return dt.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)


def get_date_range(start_date: datetime.date, end_date: datetime.date) -> List[datetime.date]:
    """
    Get a list of dates in a range.

    Args:
        start_date: Start date
        end_date: End date

    Returns:
        List of dates
    """
    delta = end_date - start_date
    return [start_date + datetime.timedelta(days=i) for i in range(delta.days + 1)]


def get_datetime_range(
    start_dt: datetime.datetime, end_dt: datetime.datetime, delta: datetime.timedelta
) -> List[datetime.datetime]:
    """
    Get a list of datetimes in a range with a specified interval.

    Args:
        start_dt: Start datetime
        end_dt: End datetime
        delta: Interval

    Returns:
        List of datetimes
    """
    result = []
    current = start_dt
    while current <= end_dt:
        result.append(current)
        current += delta
    return result


def is_leap_year(year: int) -> bool:
    """
    Check if a year is a leap year.

    Args:
        year: Year to check

    Returns:
        True if the year is a leap year, False otherwise
    """
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def get_days_in_month(year: int, month: int) -> int:
    """
    Get the number of days in a month.

    Args:
        year: Year
        month: Month (1-12)

    Returns:
        Number of days in the month

    Raises:
        ValueError: If the month is invalid
    """
    if month < 1 or month > 12:
        raise ValueError("Month must be between 1 and 12") from e

    if month == 2:
        return 29 if is_leap_year(year) else 28
    elif month in [4, 6, 9, 11]:
        return 30
    else:
        return 31


def get_days_in_year(year: int) -> int:
    """
    Get the number of days in a year.

    Args:
        year: Year

    Returns:
        Number of days in the year
    """
    return 366 if is_leap_year(year) else 365


def get_quarter(dt: datetime.datetime) -> int:
    """
    Get the quarter of the year for a datetime.

    Args:
        dt: Datetime

    Returns:
        Quarter (1-4)
    """
    return (dt.month - 1) // 3 + 1


def get_start_of_quarter(dt: datetime.datetime) -> datetime.datetime:
    """
    Get the start of the quarter for a datetime.

    Args:
        dt: Datetime

    Returns:
        Datetime at the start of the quarter
    """
    quarter = get_quarter(dt)
    month = (quarter - 1) * 3 + 1
    return dt.replace(month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def get_end_of_quarter(dt: datetime.datetime) -> datetime.datetime:
    """
    Get the end of the quarter for a datetime.

    Args:
        dt: Datetime

    Returns:
        Datetime at the end of the quarter
    """
    quarter = get_quarter(dt)
    month = quarter * 3
    return get_end_of_month(dt.replace(month=month))


def get_week_number(dt: datetime.datetime) -> int:
    """
    Get the week number of the year for a datetime.

    Args:
        dt: Datetime

    Returns:
        Week number (1-53)
    """
    return dt.isocalendar()[1]


def get_day_of_week(dt: datetime.datetime) -> int:
    """
    Get the day of the week for a datetime.

    Args:
        dt: Datetime

    Returns:
        Day of the week (1-7, where 1 is Monday)
    """
    return dt.isocalendar()[2]


def get_day_name(dt: datetime.datetime, short: bool = False) -> str:
    """
    Get the name of the day of the week for a datetime.

    Args:
        dt: Datetime
        short: Whether to return the short name

    Returns:
        Day name
    """
    if short:
        return dt.strftime("%a")
    else:
        return dt.strftime("%A")


def get_month_name(dt: datetime.datetime, short: bool = False) -> str:
    """
    Get the name of the month for a datetime.

    Args:
        dt: Datetime
        short: Whether to return the short name

    Returns:
        Month name
    """
    if short:
        return dt.strftime("%b")
    else:
        return dt.strftime("%B")
