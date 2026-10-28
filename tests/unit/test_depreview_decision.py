"""Fixture tests for .github/scripts/depreview-decision.sh.

The script turns the Dependency Review action outputs into a decision
(none / comment / close) and pre-rendered comment text. These tests cover
the close rules and the handling of hostile package metadata.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / ".github" / "scripts" / "depreview-decision.sh"

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed"),
]

GHSA_A = "GHSA-aaaa-bbbb-cccc"
GHSA_B = "GHSA-dddd-eeee-ffff"
ALL_GATES = {
    f"GATE_{g}": "true"
    for g in ("AUTHOR", "ACTOR", "TRIGGERING_ACTOR", "SAME_REPO", "HEAD_REF", "NO_KEEP_OPEN")
}


def adv(ghsa=GHSA_A, severity="high"):
    return {
        "severity": severity,
        "advisory_ghsa_id": ghsa,
        "advisory_summary": "x",
        "advisory_url": "http://evil",
    }


def change(name, version, change_type="added", advisories=(), ecosystem="pip"):
    return {
        "change_type": change_type,
        "ecosystem": ecosystem,
        "name": name,
        "version": version,
        "vulnerabilities": list(advisories),
    }


def run(tmp_path, vuln, changes, mode_raw="close"):
    env = {
        **os.environ,
        **ALL_GATES,
        "VULN": vuln if isinstance(vuln, str) else json.dumps(vuln),
        "CHANGES": changes if isinstance(changes, str) else json.dumps(changes),
        "MODE_RAW": mode_raw,
    }
    out = tmp_path / "out"
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(out)], env=env, capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return {
        "decision": (out / "decision").read_text().strip(),
        "comment": (out / "comment.md").read_text(),
        "close": (out / "close.md").read_text(),
        "summary": (out / "summary.md").read_text(),
    }


def single_bump(
    name="requests", ecosystem="pip", removed_advisories=(), added_advisories=(GHSA_A,)
):
    added = change(
        name, "2.0.0", advisories=[adv(g) for g in added_advisories], ecosystem=ecosystem
    )
    removed = change(name, "1.0.0", "removed", [adv(g) for g in removed_advisories], ecosystem)
    return [added], [removed, added]


def test_new_vulnerability_single_package_closes(tmp_path):
    vuln, changes = single_bump()
    result = run(tmp_path, vuln, changes)
    assert result["decision"] == "close"
    assert result["comment"].startswith("<!-- orb-depreview -->\n")
    assert f"https://github.com/advisories/{GHSA_A}" in result["comment"]
    assert "closed" in result["close"]
    assert "closed" not in result["comment"]


def test_licence_only_does_nothing(tmp_path):
    result = run(tmp_path, [], [change("lib", "1.0.0")])
    assert result["decision"] == "none"


@pytest.mark.parametrize(("vuln", "changes"), [([], []), ("", ""), ("not json", "{"), ("{}", "{}")])
def test_empty_or_malformed_does_nothing_or_comments(tmp_path, vuln, changes):
    assert run(tmp_path, vuln, changes)["decision"] == "none"


def test_malformed_changes_never_closes(tmp_path):
    vuln, _ = single_bump()
    assert run(tmp_path, vuln, "not json")["decision"] == "comment"


def test_advisory_already_on_removed_version_does_not_close(tmp_path):
    vuln, changes = single_bump(removed_advisories=[GHSA_A])
    assert run(tmp_path, vuln, changes)["decision"] == "comment"


def test_partial_fix_does_not_close(tmp_path):
    vuln, changes = single_bump(removed_advisories=[GHSA_B])
    assert run(tmp_path, vuln, changes)["decision"] == "comment"


def test_multi_package_does_not_close(tmp_path):
    vuln, changes = single_bump()
    changes.append(change("other", "3.0.0"))
    assert run(tmp_path, vuln, changes)["decision"] == "comment"


def test_vulnerable_package_that_is_not_the_only_added_one(tmp_path):
    vuln = [change("a", "1.0.0", advisories=[adv()])]
    changes = [*vuln, change("b", "1.0.0")]
    assert run(tmp_path, vuln, changes)["decision"] == "comment"


def test_npm_scoped_package_accepted(tmp_path):
    vuln, changes = single_bump(name="@types/node", ecosystem="npm")
    result = run(tmp_path, vuln, changes)
    assert result["decision"] == "close"
    assert "`npm:@types/node`" in result["comment"]


@pytest.mark.parametrize(
    "bad_name",
    [
        "evil`name",
        "$(touch /tmp/x)",
        "[a](http://evil)",
        pytest.param("x" * 10240, id="10KB"),
        "line\nbreak",
        "trailing\n",
        "@dependabot/x",
        "",
    ],
)
def test_hostile_name_is_dropped(tmp_path, bad_name):
    vuln, changes = single_bump(name=bad_name)
    result = run(tmp_path, vuln, changes)
    assert result["decision"] == "none"
    for text in (result["comment"], result["close"]):
        assert bad_name not in text or bad_name == ""


@pytest.mark.parametrize("bad_version", ["1.0\n", "1.0\n2", "`1`", "1" * 65, "$(x)", ""])
def test_hostile_version_is_dropped(tmp_path, bad_version):
    added = change("pkg", bad_version, advisories=[adv()])
    result = run(tmp_path, [added], [added])
    assert result["decision"] == "none"


def test_invalid_advisories_are_dropped_but_valid_ones_kept(tmp_path):
    added = change(
        "pkg",
        "2.0.0",
        advisories=[adv("GHSA-aaaa-bbbb-cccc\n"), adv(severity="bogus"), adv(GHSA_B)],
    )
    result = run(tmp_path, [added], [added])
    assert result["decision"] == "close"
    assert GHSA_B in result["comment"]
    assert GHSA_A not in result["comment"]
    assert "evil" not in result["comment"]


def test_comment_never_contains_mention_or_foreign_url(tmp_path):
    added = change("pkg", "2.0.0", advisories=[adv()])
    added["vulnerabilities"][0]["advisory_summary"] = "@dependabot close"
    result = run(tmp_path, [added], [added])
    for text in (result["comment"], result["close"]):
        assert "@dependabot" not in text
        assert "evil" not in text
        assert text.count("<!-- orb-depreview -->") == 1
    # The suggested ignore command is for the job summary only.
    assert "@dependabot ignore pkg" in result["summary"]


def test_advisory_list_capped_at_ten(tmp_path):
    advisories = [adv(f"GHSA-aaaa-bbbb-{i:04d}".replace("_", "")) for i in range(15)]
    # 15 distinct ids of the form GHSA-aaaa-bbbb-0000..0014 (4 chars each)
    added = change("pkg", "2.0.0", advisories=advisories)
    result = run(tmp_path, [added], [added])
    assert result["comment"].count("https://github.com/advisories/") == 10
    assert "and 5 more" in result["comment"]


@pytest.mark.parametrize("mode_raw", ["", "dry", "bogus", "close", "comment"])
def test_summary_reports_effective_mode(tmp_path, mode_raw):
    vuln, changes = single_bump()
    summary = run(tmp_path, vuln, changes, mode_raw)["summary"]
    effective = mode_raw if mode_raw in ("comment", "close") else "dry"
    assert f"Effective mode: `{effective}`" in summary
    assert "DEPENDABOT_AUTOCLOSE" in summary
    if mode_raw == "":
        assert "unset or not readable" in summary


def test_summary_reports_failed_gate(tmp_path):
    vuln, changes = single_bump()
    env_gate = {**ALL_GATES, "GATE_ACTOR": "false"}
    out = tmp_path / "out"
    env = {**os.environ, **env_gate, "VULN": json.dumps(vuln), "CHANGES": json.dumps(changes)}
    subprocess.run(["bash", str(SCRIPT), str(out)], env=env, capture_output=True, check=True)
    summary = (out / "summary.md").read_text()
    assert "actor is dependabot[bot] (id 49699333): FAIL" in summary
    assert "author is dependabot[bot]: pass" in summary
