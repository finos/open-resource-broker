"""Braille dot-precise progress bar for CLI watch displays."""

from rich.progress import BarColumn
from rich.text import Text

# Braille dots are in a 2x4 grid per character.
# Dot positions (bit values):
#   0x01  0x08
#   0x02  0x10
#   0x04  0x20
#   0x40  0x80
# Fill column-by-column, top-to-bottom: left col first, then right col.
# That gives us 8 steps per character.

FILL_ORDER = (0x01, 0x02, 0x04, 0x40, 0x08, 0x10, 0x20, 0x80)
DOTS_PER_CHAR = 8
BRAILLE_BASE = 0x2800
EMPTY = chr(BRAILLE_BASE)
FULL = chr(BRAILLE_BASE | 0xFF)

OD_STYLE = "bold bright_green"
SPOT_STYLE = "bold #ffdb47"


def braille_char(dots_lit: int) -> str:
    """Return a braille character with exactly `dots_lit` dots filled (0-8)."""
    bits = 0
    for i in range(dots_lit):
        bits |= FILL_ORDER[i]
    return chr(BRAILLE_BASE | bits)


class DotPreciseBar(BarColumn):
    """Two-segment braille bar: on-demand (green) then spot (cyan)."""

    def __init__(self, bar_width: int = 40) -> None:
        super().__init__(bar_width=bar_width)

    def render(self, task) -> Text:  # type: ignore[override]
        width = self.bar_width or 40
        total_dots = width * DOTS_PER_CHAR
        total = int(task.total) if task.total else total_dots

        # Use capacity units if weighted (od_cap > 0), otherwise machine counts
        od_cap = int(task.fields.get("od_cap", 0))
        spot_cap = int(task.fields.get("spot_cap", 0))
        if od_cap or spot_cap:
            od = od_cap
            spot = spot_cap
        else:
            od = int(task.fields.get("od_machines", 0))
            spot = int(task.fields.get("spot_machines", 0))

        od_dots = min(int(od * total_dots / total), total_dots) if total else 0
        spot_dots = min(int(spot * total_dots / total), total_dots - od_dots) if total else 0

        bar = Text()
        bar.append("[", style="bold white")
        bar = _append_segment(bar, od_dots, OD_STYLE)
        bar = _append_segment(bar, spot_dots, SPOT_STYLE)

        filled_dots = od_dots + spot_dots
        filled_chars = (filled_dots + DOTS_PER_CHAR - 1) // DOTS_PER_CHAR if filled_dots else 0
        empty_chars = width - filled_chars
        if empty_chars > 0:
            bar.append(EMPTY * empty_chars, style="dim")
        bar.append("]", style="bold white")

        return bar


def _append_segment(bar: Text, dots: int, style: str) -> Text:
    """Append a segment of braille dots in the given style."""
    if dots <= 0:
        return bar
    full_chars = dots // DOTS_PER_CHAR
    partial = dots % DOTS_PER_CHAR
    for _ in range(full_chars):
        bar.append(FULL, style=style)
    if partial:
        bar.append(braille_char(partial), style=style)
    return bar


def render_az_bars(
    az_stats: dict[str, dict[str, int]],
    total_capacity: int,
    bar_width: int = 30,
) -> Text:
    """Render per-AZ progress bars on one line.

    Uses the same dots-per-unit ratio as the top bar so scales match.
    Each AZ bar width is determined by its data, not pre-allocated.
    """
    if not az_stats:
        return Text("")

    top_bar_dots = bar_width * DOTS_PER_CHAR
    dots_per_unit = top_bar_dots / total_capacity if total_capacity else 0

    sorted_azs = sorted(az_stats.keys())
    result = Text()

    for az in sorted_azs:
        short_az = az[-2:] if len(az) > 2 else az
        result.append(f"{short_az} ", style="dim")

        s = az_stats[az]
        od_cap = s.get("od_cap", 0)
        spot_cap = s.get("spot_cap", 0)
        if od_cap or spot_cap:
            od = od_cap
            spot = spot_cap
        else:
            od = s.get("od_machines", 0)
            spot = s.get("spot_machines", 0)

        od_dots = int(od * dots_per_unit)
        spot_dots = int(spot * dots_per_unit)
        filled_dots = od_dots + spot_dots

        # Bar width = enough chars to hold filled dots + at least 1 empty char for unfilled AZs
        bar_chars = (filled_dots + DOTS_PER_CHAR - 1) // DOTS_PER_CHAR if filled_dots else 1
        total_dots = bar_chars * DOTS_PER_CHAR
        empty_dots = total_dots - filled_dots

        result.append("[", style="bold white")
        result = _append_segment(result, od_dots, OD_STYLE)
        result = _append_segment(result, spot_dots, SPOT_STYLE)
        empty_chars = empty_dots // DOTS_PER_CHAR
        if empty_chars > 0:
            result.append(EMPTY * empty_chars, style="dim")
        result.append("]", style="bold white")
        result.append(" ")

    return result
