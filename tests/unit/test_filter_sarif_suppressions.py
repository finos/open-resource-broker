"""Tests for the SARIF filter that drops in-source-suppressed Semgrep results.

Semgrep honours ``# nosemgrep`` on the command line but still writes the
suppressed results into its SARIF output, so GitHub code scanning keeps them
open. The filter removes every result whose suppression is accepted (SARIF
2.1.0: a missing ``state`` means accepted) and keeps everything else.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
_DEV_TOOLS_PATH = str(ROOT / "dev-tools" / "ci")
if _DEV_TOOLS_PATH not in sys.path:
    sys.path.insert(0, _DEV_TOOLS_PATH)

from filter_sarif_suppressions import filter_sarif, main  # type: ignore[import]  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "reusable-security.yml"


def _result(rule_id: str, suppressions: list | None = None) -> dict:
    result: dict = {"ruleId": rule_id, "message": {"text": rule_id}}
    if suppressions is not None:
        result["suppressions"] = suppressions
    return result


def _sarif() -> dict:
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "Semgrep OSS"}},
                "results": [
                    _result("in-source", [{"kind": "inSource"}]),
                    _result("accepted", [{"state": "accepted"}]),
                    _result("unsuppressed"),
                    _result("empty-suppressions", []),
                    _result("rejected", [{"kind": "external", "state": "rejected"}]),
                    _result("under-review", [{"kind": "external", "state": "underReview"}]),
                ],
            }
        ],
    }


def _rule_ids(sarif: dict) -> list[str]:
    return [r["ruleId"] for run in sarif["runs"] for r in run.get("results", [])]


@pytest.mark.unit
def test_drops_only_accepted_suppressions() -> None:
    filtered = filter_sarif(_sarif())
    assert _rule_ids(filtered) == ["unsuppressed", "empty-suppressions", "rejected", "under-review"]


@pytest.mark.unit
def test_cli_writes_new_file_and_leaves_input_untouched(tmp_path: Path) -> None:
    src = tmp_path / "semgrep.sarif"
    dst = tmp_path / "semgrep-filtered.sarif"
    original = json.dumps(_sarif())
    src.write_text(original)

    assert main([str(src), str(dst)]) == 0

    assert src.read_text() == original
    assert _rule_ids(json.loads(dst.read_text())) == [
        "unsuppressed",
        "empty-suppressions",
        "rejected",
        "under-review",
    ]


@pytest.mark.unit
def test_run_without_results_is_kept(tmp_path: Path) -> None:
    src = tmp_path / "in.sarif"
    dst = tmp_path / "out.sarif"
    src.write_text(json.dumps({"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "x"}}}]}))
    assert main([str(src), str(dst)]) == 0
    assert json.loads(dst.read_text())["runs"][0]["tool"]["driver"]["name"] == "x"


@pytest.mark.unit
@pytest.mark.parametrize(
    "content",
    ["{not json", json.dumps({"version": "2.1.0"}), json.dumps({"runs": "nope"}), json.dumps([1])],
)
def test_invalid_input_exits_non_zero(tmp_path: Path, content: str) -> None:
    src = tmp_path / "in.sarif"
    dst = tmp_path / "out.sarif"
    src.write_text(content)
    assert main([str(src), str(dst)]) != 0
    assert not dst.exists()


@pytest.mark.unit
def test_missing_input_exits_non_zero(tmp_path: Path) -> None:
    dst = tmp_path / "out.sarif"
    assert main([str(tmp_path / "absent.sarif"), str(dst)]) != 0
    assert not dst.exists()


@pytest.mark.unit
def test_workflow_uploads_filtered_sarif() -> None:
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["security-scan"]["steps"]
    names = [s.get("name", "") for s in steps]

    run_idx = names.index("Run Semgrep security analysis")
    upload_idx = names.index("Upload Semgrep SARIF results")
    filter_idx = next(
        i for i, s in enumerate(steps) if "filter_sarif_suppressions.py" in s.get("run", "")
    )

    assert run_idx < filter_idx < upload_idx

    filter_step = steps[filter_idx]
    upload_step = steps[upload_idx]
    assert " ".join(filter_step["if"].split()) == " ".join(upload_step["if"].split())
    assert "always()" in filter_step["if"]
    assert "semgrep.sarif semgrep-filtered.sarif" in filter_step["run"]
    assert upload_step["with"]["sarif_file"] == "semgrep-filtered.sarif"
