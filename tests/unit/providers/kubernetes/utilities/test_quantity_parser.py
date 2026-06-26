"""Unit tests for :mod:`orb.providers.kubernetes.utilities.quantity_parser`.

Covers the CPU and memory quantity-string parsers copied as new code from
the legacy ``k8sutils.py``.  Test cases mirror the behaviour the legacy
unit tests pinned, plus a few edge cases (empty / None / unrecognised
trailing characters).
"""

from __future__ import annotations

import pytest

from orb.providers.kubernetes.utilities.quantity_parser import (
    parse_cpu_quantity,
    parse_memory_quantity,
)

# ---------------------------------------------------------------------------
# CPU parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("500m", 0.5),
        ("250m", 0.25),
        ("1000m", 1.0),
        ("1", 1.0),
        ("2", 2.0),
        ("2.5", 2.5),
        ("0.5", 0.5),
    ],
)
def test_parse_cpu_quantity_happy_path(value: str, expected: float) -> None:
    assert parse_cpu_quantity(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", ["", None])
def test_parse_cpu_quantity_empty_returns_zero(value: str | None) -> None:
    assert parse_cpu_quantity(value) == 0.0


def test_parse_cpu_quantity_rejects_non_numeric() -> None:
    with pytest.raises(ValueError):
        parse_cpu_quantity("notacpu")


def test_parse_cpu_quantity_millicore_must_be_integer() -> None:
    # Millicore prefix expects an int; non-int raises during int(...)
    with pytest.raises(ValueError):
        parse_cpu_quantity("0.5m")


# ---------------------------------------------------------------------------
# Memory parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("512Ki", 512 * 1024),
        ("256Mi", 256 * 1024 * 1024),
        ("2Gi", 2 * 1024 * 1024 * 1024),
        ("500k", 500 * 1000),
        ("500M", 500 * 1000 * 1000),
        ("2G", 2 * 1000 * 1000 * 1000),
        ("1024", 1024),
        ("0", 0),
    ],
)
def test_parse_memory_quantity_happy_path(value: str, expected: int) -> None:
    assert parse_memory_quantity(value) == expected


@pytest.mark.parametrize("value", ["", None])
def test_parse_memory_quantity_empty_returns_zero(value: str | None) -> None:
    assert parse_memory_quantity(value) == 0


def test_parse_memory_quantity_rejects_unknown_suffix() -> None:
    # The parser only knows fixed suffixes; an unknown suffix falls through
    # to ``int(...)`` which raises.
    with pytest.raises(ValueError):
        parse_memory_quantity("1Ti")
