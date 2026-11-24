"""Tests for the Semgrep result-count canary used when the Semgrep project changes."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_DEV_TOOLS_PATH = str(ROOT / "dev-tools" / "ci")
if _DEV_TOOLS_PATH not in sys.path:
    sys.path.insert(0, _DEV_TOOLS_PATH)

from semgrep_baseline_check import main  # type: ignore[import]


def _sarif(tmp_path: Path, name: str, counts: list[int]) -> str:
    path = tmp_path / name
    runs = [{"results": [{"ruleId": f"r{i}"} for i in range(n)]} for n in counts]
    path.write_text(json.dumps({"runs": runs}))
    return str(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("baseline", "current", "code"), [(5, 5, 0), (5, 7, 0), (5, 4, 1), (0, 0, 0)]
)
def test_only_a_drop_in_results_fails(
    tmp_path: Path, baseline: int, current: int, code: int
) -> None:
    assert main([_sarif(tmp_path, "b", [baseline]), _sarif(tmp_path, "c", [current])]) == code


@pytest.mark.unit
def test_results_are_summed_across_runs(tmp_path: Path) -> None:
    assert main([_sarif(tmp_path, "b", [2, 2]), _sarif(tmp_path, "c", [3])]) == 1


@pytest.mark.unit
def test_unreadable_or_missing_sarif_fails(tmp_path: Path) -> None:
    good = _sarif(tmp_path, "g", [1])
    (tmp_path / "bad").write_text("not json")
    assert main([good, str(tmp_path / "bad")]) == 1
    assert main([good, str(tmp_path / "missing")]) == 1
    assert main([good]) == 2
