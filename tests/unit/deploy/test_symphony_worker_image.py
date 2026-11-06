"""The Symphony worker image sources: Dockerfile, shipped scripts and the liveness probe."""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKER_DIR = REPO_ROOT / "deploy" / "symphony-worker"
BIN_DIR = WORKER_DIR / "bin"
LIVENESS = BIN_DIR / "compute-liveness"

SHIPPED_SCRIPTS = {"compute-liveness", "configure-pod-params", "symphony-exporter-bootstrap"}

# Answers the two calls compute-liveness makes: `pgrep -x lim` prints the lim pid
# (or nothing), and `pgrep -P <pid>` exits 0 when lim has children.
_FAKE_PGREP = """#!/bin/sh
if [ "$1" = "-x" ]; then
  [ -n "$FAKE_LIM_PID" ] && echo "$FAKE_LIM_PID"
  exit 0
fi
if [ "$1" = "-P" ]; then
  [ "$FAKE_LIM_HAS_CHILDREN" = "1" ] && exit 0
  exit 1
fi
exit 2
"""


def _tracked_bin_entries() -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "ls-files", "-s", "deploy/symphony-worker/bin"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    entries = []
    for line in out:
        meta, path = line.split("\t", 1)
        entries.append((meta.split()[0], path))
    return entries


def test_image_ships_only_the_compute_worker_scripts():
    names = {Path(path).name for _, path in _tracked_bin_entries()}
    assert names == SHIPPED_SCRIPTS


def test_shipped_scripts_are_executable_in_git():
    entries = _tracked_bin_entries()
    assert entries
    for mode, path in entries:
        assert mode == "100755", f"{path} is tracked as {mode}, not executable"


def test_shipped_scripts_have_lf_line_endings():
    entries = _tracked_bin_entries()
    assert entries
    for _, path in entries:
        assert b"\r" not in (REPO_ROOT / path).read_bytes(), f"{path} contains CR characters"


def test_dockerfile_requires_base_image_without_default():
    lines = (WORKER_DIR / "Dockerfile").read_text().splitlines()
    assert any(re.fullmatch(r"ARG BASE_IMAGE", line) for line in lines)


needs_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")


@pytest.fixture
def run_liveness(tmp_path):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    pgrep = fake_bin / "pgrep"
    pgrep.write_text(_FAKE_PGREP)
    pgrep.chmod(0o755)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    def _run(lim_pid: str = "", has_children: bool = False) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "ORB_WORKER_STATE_DIR": str(state_dir),
            "FAKE_LIM_PID": lim_pid,
            "FAKE_LIM_HAS_CHILDREN": "1" if has_children else "0",
        }
        return subprocess.run(
            ["sh", str(LIVENESS)], env=env, capture_output=True, text=True, check=False
        )

    return _run, state_dir


def _aged(seconds: int) -> str:
    return f"{int(time.time()) - seconds}\n"


@needs_sh
def test_liveness_passes_while_lim_is_missing_under_threshold(run_liveness):
    run, state_dir = run_liveness
    first = run()
    assert first.returncode == 0
    assert (state_dir / "lim_missing_since").is_file()
    assert run().returncode == 0


@needs_sh
def test_liveness_fails_when_lim_missing_past_threshold(run_liveness):
    run, state_dir = run_liveness
    (state_dir / "lim_missing_since").write_text(_aged(600))
    result = run()
    assert result.returncode == 1
    assert "lim missing" in result.stdout


@needs_sh
def test_liveness_fails_when_lim_has_no_children_past_threshold(run_liveness):
    run, state_dir = run_liveness
    (state_dir / "lim_missing_since").write_text(_aged(30))
    (state_dir / "lim_no_child_since").write_text(_aged(600))
    result = run(lim_pid="4242", has_children=False)
    assert result.returncode == 1
    assert "no children" in result.stdout
    assert not (state_dir / "lim_missing_since").exists()


@needs_sh
def test_liveness_passes_and_clears_state_when_lim_has_children(run_liveness):
    run, state_dir = run_liveness
    (state_dir / "lim_no_child_since").write_text(_aged(600))
    result = run(lim_pid="4242", has_children=True)
    assert result.returncode == 0
    assert not (state_dir / "lim_no_child_since").exists()
