"""Tests for dev-tools/release/build_historical.sh.

The script builds an old commit in a temporary worktree, overlaying a modern
pyproject.toml, and leaves the wheel in the caller's dist/. These tests run it in a throwaway git
repository with a fake ``python`` on PATH: its ``-m pip install -r FILE`` step
records that FILE existed, and its ``-m build`` step fails unless that
happened, so the build tooling must really be installed from the lock file.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "dev-tools" / "release" / "build_historical.sh"

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(os.name == "nt", reason="POSIX shell script"),
    pytest.mark.skipif(shutil.which("git") is None, reason="git not installed"),
]

_FAKE_PYTHON = """#!/bin/sh
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
    lock=""
    while [ $# -gt 0 ]; do
        if [ "$1" = "-r" ]; then lock="$2"; fi
        shift
    done
    if [ -n "$FAKE_PIP_FAIL" ] || [ ! -f "$lock" ]; then exit 1; fi
    touch "$FAKE_MARKER"
    exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "build" ]; then
    [ -f "$FAKE_MARKER" ] || exit 1
    # The build runs in the historical tree with the overlay applied
    [ -f pyproject.toml ] || exit 1
    outdir=""
    while [ $# -gt 0 ]; do
        if [ "$1" = "--outdir" ]; then outdir="$2"; fi
        shift
    done
    [ -n "$outdir" ] || exit 1
    mkdir -p "$outdir"
    touch "$outdir/open_resource_broker-0.0.1-py3-none-any.whl"
    exit 0
fi
exit 1
"""


def _clean_env(tmp_path: Path) -> dict[str, str]:
    """An environment that cannot point git at any repository but the test one.

    Git hooks export GIT_DIR, GIT_INDEX_FILE and friends; inherited, they would
    aim the script's checkout and clean at the developer's own checkout.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["HOME"] = str(tmp_path / "home")
    return env


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, dict[str, str], str]:
    """A repository whose HEAD has the script and lock, and an older commit without them."""
    env = _clean_env(tmp_path)
    (tmp_path / "home").mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, env, "init", "-q", "-b", "main")
    (root / "src").mkdir()
    (root / "src" / "__init__.py").write_text("")
    (root / "README.md").write_text("old\n")
    _git(root, env, "add", "-A")
    _git(root, env, "commit", "-q", "-m", "old")
    old = _git(root, env, "rev-parse", "HEAD")

    lock = root / ".github" / "requirements" / "package-build.txt"
    lock.parent.mkdir(parents=True)
    lock.write_text("build==1.0 --hash=sha256:00\n")
    script = root / "dev-tools" / "release" / "build_historical.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, script)
    _git(root, env, "add", "-A")
    _git(root, env, "commit", "-q", "-m", "new")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "python"
    fake.write_text(_FAKE_PYTHON)
    fake.chmod(0o755)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_MARKER"] = str(tmp_path / "pip-installed-from-lock")
    return root, env, old


def _run(root: Path, env: dict[str, str], commit: str) -> subprocess.CompletedProcess[str]:
    script = root / "dev-tools" / "release" / "build_historical.sh"
    return subprocess.run(
        ["bash", str(script), commit, "0.0.1"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _temp_branches(root: Path, env: dict[str, str]) -> str:
    return _git(root, env, "branch", "--list", "historical-build-*")


def _assert_caller_untouched(root: Path, env: dict[str, str]) -> None:
    assert _git(root, env, "branch", "--show-current") == "main"
    assert _temp_branches(root, env) == ""
    # Only the caller's own checkout remains registered
    assert len(_git(root, env, "worktree", "list").splitlines()) == 1
    assert (root / "notes.txt").read_text() == "caller's untracked work\n"
    assert (root / "README.md").read_text() == "new, edited locally\n"
    assert not (root / "pyproject.toml").exists()
    assert _git(root, env, "diff", "--name-only") == "README.md"
    untracked = _git(root, env, "ls-files", "--others", "--exclude-standard").splitlines()
    assert [f for f in untracked if not f.startswith("dist/")] == ["notes.txt"]


@pytest.fixture
def dirty_repo(repo) -> tuple[Path, dict[str, str], str]:
    root, env, old = repo
    (root / "notes.txt").write_text("caller's untracked work\n")
    (root / "README.md").write_text("new, edited locally\n")
    return root, env, old


def test_build_tooling_is_installed_from_the_lock(dirty_repo) -> None:
    root, env, old = dirty_repo

    result = _run(root, env, old)

    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(env["FAKE_MARKER"]).exists()
    # The wheel is left in the caller's dist/ and the caller's checkout is untouched
    assert (root / "dist" / "open_resource_broker-0.0.1-py3-none-any.whl").exists()
    _assert_caller_untouched(root, env)


def test_failed_install_leaves_the_caller_untouched(dirty_repo) -> None:
    root, env, old = dirty_repo
    env["FAKE_PIP_FAIL"] = "1"

    result = _run(root, env, old)

    assert result.returncode != 0
    assert not (root / "dist").exists()
    _assert_caller_untouched(root, env)


def test_detached_caller_stays_on_its_commit(repo) -> None:
    root, env, old = repo
    head = _git(root, env, "rev-parse", "HEAD")
    _git(root, env, "checkout", "-q", "--detach", head)

    result = _run(root, env, old)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(root, env, "branch", "--show-current") == ""
    assert _git(root, env, "rev-parse", "HEAD") == head
    assert len(_git(root, env, "worktree", "list").splitlines()) == 1
