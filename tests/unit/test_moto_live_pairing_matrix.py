"""Every live AWS test file is paired in the moto/live matrix of moto-limits.md."""

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_AWS_TESTS = _REPO / "tests" / "providers" / "aws"
_DOC = _REPO / "docs" / "root" / "testing" / "moto-limits.md"


def _matrix_rows() -> dict[str, str]:
    """Map each live file named in the pairing table to its moto column."""
    text = _DOC.read_text()
    section = text.split("## Live/moto pairing", 1)[1].split("\n## ", 1)[0]
    rows = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0].startswith("`test_"):
            rows[cells[0].strip("`")] = cells[1]
    return rows


def test_every_live_test_file_is_in_the_pairing_matrix():
    live_files = {p.name for p in (_AWS_TESTS / "live").glob("test_*.py")}

    assert live_files, "no live test files found"
    assert live_files == set(_matrix_rows())


def test_moto_counterparts_named_in_the_matrix_exist():
    for live_file, moto_cell in _matrix_rows().items():
        for path in re.findall(r"`([^`]+\.py)`", moto_cell):
            assert (_AWS_TESTS / path).is_file(), f"{live_file}: {path} does not exist"
