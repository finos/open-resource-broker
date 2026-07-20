"""Unit tests for orb.cli.progress_bar.

Covers braille_char bit patterns, _append_segment character assembly,
DotPreciseBar.render capacity logic, and render_az_bars.
"""

from __future__ import annotations

import pytest

# The production module ``orb.cli.progress_bar`` imports ``rich`` unconditionally
# at module scope. ``rich`` is an optional dependency that is absent in the
# minimal CI unit-test environment, so skip the whole module cleanly when it is
# unavailable rather than erroring on collection.
pytest.importorskip("rich")


@pytest.mark.unit
class TestBrailleChar:
    """braille_char must return the correct Unicode braille character."""

    def test_zero_dots_returns_empty_braille(self):
        from orb.cli.progress_bar import BRAILLE_BASE, braille_char

        result = braille_char(0)
        assert ord(result) == BRAILLE_BASE

    def test_eight_dots_returns_full_braille(self):
        from orb.cli.progress_bar import FULL, braille_char

        result = braille_char(8)
        assert result == FULL

    def test_one_dot_single_bit(self):
        from orb.cli.progress_bar import BRAILLE_BASE, FILL_ORDER, braille_char

        result = braille_char(1)
        expected = chr(BRAILLE_BASE | FILL_ORDER[0])
        assert result == expected

    def test_four_dots_middle(self):
        from orb.cli.progress_bar import BRAILLE_BASE, FILL_ORDER, braille_char

        result = braille_char(4)
        bits = 0
        for i in range(4):
            bits |= FILL_ORDER[i]
        assert ord(result) == BRAILLE_BASE | bits

    def test_results_differ_for_each_dot_count(self):
        from orb.cli.progress_bar import braille_char

        chars = [braille_char(i) for i in range(9)]
        # All should be distinct
        assert len(set(chars)) == 9


@pytest.mark.unit
class TestAppendSegment:
    """_append_segment appends the right number of braille chars."""

    def test_zero_dots_unchanged(self):
        from rich.text import Text

        from orb.cli.progress_bar import OD_STYLE, _append_segment

        bar = Text()
        result = _append_segment(bar, 0, OD_STYLE)
        assert str(result) == ""

    def test_exactly_one_char_width(self):
        from rich.text import Text

        from orb.cli.progress_bar import DOTS_PER_CHAR, FULL, OD_STYLE, _append_segment

        bar = Text()
        result = _append_segment(bar, DOTS_PER_CHAR, OD_STYLE)
        assert str(result) == FULL

    def test_partial_dots_last_char_is_partial(self):
        from rich.text import Text

        from orb.cli.progress_bar import (
            DOTS_PER_CHAR,
            FULL,
            OD_STYLE,
            _append_segment,
            braille_char,
        )

        # 9 dots = 1 full + 1 partial (1 extra dot)
        bar = Text()
        result = _append_segment(bar, DOTS_PER_CHAR + 1, OD_STYLE)
        text_str = str(result)
        assert FULL in text_str
        assert braille_char(1) in text_str

    def test_two_full_chars(self):
        from rich.text import Text

        from orb.cli.progress_bar import DOTS_PER_CHAR, FULL, OD_STYLE, _append_segment

        bar = Text()
        result = _append_segment(bar, DOTS_PER_CHAR * 2, OD_STYLE)
        assert str(result) == FULL * 2

    def test_negative_dots_unchanged(self):
        from rich.text import Text

        from orb.cli.progress_bar import OD_STYLE, _append_segment

        bar = Text()
        result = _append_segment(bar, -5, OD_STYLE)
        assert str(result) == ""


@pytest.mark.unit
class TestDotPreciseBar:
    """DotPreciseBar.render produces a Rich Text object with brackets."""

    def _make_task(self, total=100, **fields):
        from unittest.mock import MagicMock

        task = MagicMock()
        task.total = total
        task.fields = fields
        return task

    def test_render_returns_text_with_brackets(self):
        from orb.cli.progress_bar import DotPreciseBar

        bar = DotPreciseBar(bar_width=10)
        task = self._make_task(total=100, od_machines=30, spot_machines=20)
        result = bar.render(task)
        text_str = str(result)
        assert "[" in text_str
        assert "]" in text_str

    @staticmethod
    def _inner_braille(text_str: str) -> list[str]:
        """Return the braille characters rendered between the [ ] brackets."""
        assert text_str.startswith("[")
        assert text_str.endswith("]")
        inner = text_str[1:-1]
        return [ch for ch in inner if 0x2800 <= ord(ch) <= 0x28FF]

    def test_render_with_zero_total_does_not_crash(self):
        from orb.cli.progress_bar import FULL, DotPreciseBar

        bar = DotPreciseBar(bar_width=10)
        task = self._make_task(total=0, od_machines=0, spot_machines=0)
        result = bar.render(task)
        text_str = str(result)
        # total=0 falls back to total_dots and nothing is filled: an all-empty bar.
        assert FULL not in text_str
        assert len(self._inner_braille(text_str)) == 10

    def test_render_uses_capacity_units_when_cap_fields_set(self):
        from orb.cli.progress_bar import FULL, DotPreciseBar

        bar = DotPreciseBar(bar_width=10)
        # Capacity fields must take precedence; machine counts are deliberately 0,
        # so an all-empty bar would prove the cap fields were ignored.
        task = self._make_task(total=200, od_cap=80, spot_cap=40, od_machines=0, spot_machines=0)
        result = bar.render(task)
        text_str = str(result)
        # od 80/200 -> 32 dots (4 full), spot 40/200 -> 16 dots (2 full) = 6 full chars.
        assert text_str.count(FULL) == 6

    def test_render_caps_od_dots_at_total(self):
        from orb.cli.progress_bar import DotPreciseBar

        bar = DotPreciseBar(bar_width=10)
        # od_machines vastly exceeds total; min() must cap dots at total_dots so the
        # bar spans exactly bar_width chars with no overflow.
        task = self._make_task(total=10, od_machines=100, spot_machines=0)
        result = bar.render(task)
        text_str = str(result)
        assert len(self._inner_braille(text_str)) == 10

    def test_default_bar_width_used_when_none(self):
        from orb.cli.progress_bar import DotPreciseBar

        bar = DotPreciseBar(bar_width=0)
        task = self._make_task(total=100, od_machines=50)
        result = bar.render(task)
        text_str = str(result)
        # bar_width 0 falls back to 40, so the bar spans exactly 40 braille chars.
        assert len(self._inner_braille(text_str)) == 40


@pytest.mark.unit
class TestRenderAzBars:
    """render_az_bars produces a Rich Text object for per-AZ bars."""

    def test_empty_az_stats_returns_empty_text(self):
        from orb.cli.progress_bar import render_az_bars

        result = render_az_bars({}, total_capacity=100)
        assert str(result) == ""

    def test_single_az_label_truncated(self):
        from orb.cli.progress_bar import render_az_bars

        az_stats = {"us-east-1a": {"od_machines": 5, "spot_machines": 2}}
        result = render_az_bars(az_stats, total_capacity=10, bar_width=20)
        text_str = str(result)
        # Last 2 chars of AZ name
        assert "1a" in text_str

    def test_two_az_stats_both_rendered(self):
        from orb.cli.progress_bar import render_az_bars

        az_stats = {
            "us-east-1a": {"od_machines": 3, "spot_machines": 1},
            "us-east-1b": {"od_machines": 2, "spot_machines": 0},
        }
        result = render_az_bars(az_stats, total_capacity=10, bar_width=20)
        text_str = str(result)
        assert "1a" in text_str
        assert "1b" in text_str

    def test_capacity_units_used_when_cap_fields_set(self):
        from orb.cli.progress_bar import render_az_bars

        az_stats = {"us-east-1a": {"od_cap": 10, "spot_cap": 5}}
        result = render_az_bars(az_stats, total_capacity=20, bar_width=20)
        assert result is not None

    def test_zero_total_capacity_does_not_crash(self):
        from orb.cli.progress_bar import render_az_bars

        az_stats = {"us-east-1a": {"od_machines": 5}}
        result = render_az_bars(az_stats, total_capacity=0, bar_width=20)
        assert result is not None

    def test_az_name_shorter_than_two_chars_uses_full_name(self):
        from orb.cli.progress_bar import render_az_bars

        az_stats = {"a": {"od_machines": 1}}
        result = render_az_bars(az_stats, total_capacity=10, bar_width=10)
        text_str = str(result)
        assert "a" in text_str

    def test_az_bars_sorted_alphabetically(self):
        from orb.cli.progress_bar import render_az_bars

        az_stats = {
            "us-east-1c": {"od_machines": 1},
            "us-east-1a": {"od_machines": 2},
            "us-east-1b": {"od_machines": 3},
        }
        result = render_az_bars(az_stats, total_capacity=10, bar_width=20)
        text_str = str(result)
        pos_a = text_str.find("1a")
        pos_b = text_str.find("1b")
        pos_c = text_str.find("1c")
        assert pos_a < pos_b < pos_c
