"""Integration tests for resumeProgram.sh's API-mode payload construction.

Covers open-resource-broker-2706.2: in API mode the script built the
`node_names` JSON array via `${NODE_LIST// /", "}`, a naive space
substitution that mishandles SLURM bracket hostlists (e.g.
"compute-[001-003]" has no spaces, so it produced one malformed array
element instead of the three expanded node names).

These tests run the real script via /bin/bash with `scontrol`, `curl`, and
(for one variant) `jq` replaced by lightweight fakes on PATH, and assert the
JSON payload actually sent to the mocked ORB API contains the fully expanded
node name list.
"""

import json
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).parents[3]
    / "src"
    / "orb"
    / "infrastructure"
    / "scheduler"
    / "slurm"
    / "scripts"
    / "resumeProgram.sh"
)

_FAKE_SCONTROL = """#!/usr/bin/env python3
import re
import sys


def expand(spec):
    tokens, depth, cur = [], 0, ""
    for ch in spec:
        if ch == "[":
            depth += 1
            cur += ch
        elif ch == "]":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            tokens.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        tokens.append(cur)

    results = []
    for token in tokens:
        match = re.match(r"^(.+?)\\[(.+)\\]$", token)
        if not match:
            results.append(token)
            continue
        prefix, range_spec = match.group(1), match.group(2)
        for part in range_spec.split(","):
            if "-" in part:
                start_s, end_s = part.split("-", 1)
                width = len(start_s)
                for i in range(int(start_s), int(end_s) + 1):
                    results.append(f"{prefix}{str(i).zfill(width)}")
            else:
                results.append(f"{prefix}{part}")
    return results


args = sys.argv[1:]
if args[:2] == ["show", "hostnames"]:
    for group in args[2].split():
        for name in expand(group):
            print(name)
sys.exit(0)
"""

_FAKE_CURL = """#!/bin/bash
# Captures the JSON payload passed via -d and returns a canned 200 response.
PREV=""
for ARG in "$@"; do
    if [ "$PREV" = "-d" ]; then
        printf '%s' "$ARG" > "$CURL_CAPTURE_FILE"
    fi
    PREV="$ARG"
done
printf '{}\\n200\\n'
"""


def _write_fake(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_resume_program(tmp_path: Path, node_list: str, include_real_jq: bool) -> list[str]:
    """Run resumeProgram.sh in API mode and return the expanded node_names list sent."""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    _write_fake(fakebin / "scontrol", _FAKE_SCONTROL)
    _write_fake(fakebin / "curl", _FAKE_CURL)

    capture_file = tmp_path / "curl_payload.json"
    log_dir = tmp_path / "logs"

    if include_real_jq:
        path_env = f"{fakebin}:/opt/homebrew/bin:/usr/bin:/bin"
    else:
        # Deliberately excludes any directory that could resolve a real `jq`,
        # forcing the script's no-jq fallback loop.
        path_env = f"{fakebin}:/usr/bin:/bin"

    env = {
        "PATH": path_env,
        "HOME": str(tmp_path),
        "SLURM_ORB_MODE": "api",
        "SLURM_ORB_API_URL": "http://localhost:8000",
        "SLURM_ORB_LOG_DIR": str(log_dir),
        "ORB_ROOT_DIR": str(tmp_path),
        "CURL_CAPTURE_FILE": str(capture_file),
    }

    result = subprocess.run(
        ["/bin/bash", str(SCRIPT_PATH), node_list],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"script failed (rc={result.returncode}): stdout={result.stdout!r} "
        f"stderr={result.stderr!r} log={_read_log(log_dir)}"
    )
    assert capture_file.exists(), f"curl was never invoked with -d; log={_read_log(log_dir)}"

    payload = json.loads(capture_file.read_text())
    assert payload["request_type"] == "provision"
    return payload["node_names"]


def _read_log(log_dir: Path) -> str:
    log_file = log_dir / "resume_program.log"
    return log_file.read_text() if log_file.exists() else "<no log>"


@pytest.mark.integration
@pytest.mark.parametrize("include_real_jq", [True, False], ids=["with-jq", "without-jq"])
def test_bracket_range_expands_to_individual_node_names(tmp_path, include_real_jq):
    node_names = _run_resume_program(tmp_path, "compute-[001-003]", include_real_jq)
    assert node_names == ["compute-001", "compute-002", "compute-003"]


@pytest.mark.integration
@pytest.mark.parametrize("include_real_jq", [True, False], ids=["with-jq", "without-jq"])
def test_comma_separated_list_expands_to_individual_node_names(tmp_path, include_real_jq):
    node_names = _run_resume_program(tmp_path, "compute-001,compute-002", include_real_jq)
    assert node_names == ["compute-001", "compute-002"]


@pytest.mark.integration
def test_single_node_name_produces_single_element_array(tmp_path):
    node_names = _run_resume_program(tmp_path, "compute-001", include_real_jq=True)
    assert node_names == ["compute-001"]


@pytest.mark.integration
def test_space_separated_node_names_still_work(tmp_path):
    """Pre-existing behavior (no bracket/comma hostlist) must keep working."""
    node_names = _run_resume_program(tmp_path, "node1 node2", include_real_jq=True)
    assert node_names == ["node1", "node2"]
