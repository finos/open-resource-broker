"""The built wheel must carry every runtime data file and no stale modules."""

import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"

# Files that are documentation or tooling for the source tree only.
_NOT_SHIPPED = {".gitignore", "README.md"}
_NOT_SHIPPED_SUFFIXES = {".pyc"}


def _tracked_package_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "src/orb"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    return [Path(p) for p in out]


def _build_command() -> list[str] | None:
    if shutil.which("uv"):
        return ["uv", "build", "--wheel", "--out-dir"]
    if importlib.util.find_spec("build") is not None:
        return [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir"]
    return None


@pytest.fixture(scope="module")
def wheel_names(tmp_path_factory) -> set[str]:
    cmd = _build_command()
    if cmd is None:
        if os.environ.get("CI"):
            pytest.fail("uv or the build package must be available in CI to build the wheel")
        pytest.skip("uv or the build package is required to build the wheel")
    outdir = tmp_path_factory.mktemp("wheel")
    env = {**os.environ, "ORB_SKIP_UI_BUILD": "1"}
    subprocess.run(
        [*cmd, str(outdir)],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    wheel = next(outdir.glob("orb_py-*.whl"))
    with zipfile.ZipFile(wheel) as zf:
        return set(zf.namelist())


def test_runtime_data_files_are_in_wheel(wheel_names):
    expected = [
        "orb/providers/aws/specs/launch-template/default.json",
        "orb/providers/aws/specs/runinstances/default.json",
        "orb/providers/k8s/specs/pod/default.json",
        "orb/providers/k8s/config/k8s_defaults.json",
        "orb/config/default_config.json",
        "orb/infrastructure/storage/sql/migrations/alembic.ini",
        "orb/infrastructure/scheduler/hostfactory/scripts/invoke_provider.sh",
        "orb/infrastructure/scheduler/hostfactory/scripts/invoke_provider.bat",
        "orb/infrastructure/scheduler/slurm/scripts/resumeProgram.sh",
    ]
    assert [n for n in expected if n not in wheel_names] == []


def test_every_tracked_data_file_is_in_wheel(wheel_names):
    missing = []
    for path in _tracked_package_files():
        rel = path.relative_to("src")
        if path.suffix == ".py" or path.name in _NOT_SHIPPED or "tests" in rel.parts:
            continue
        if path.suffix in _NOT_SHIPPED_SUFFIXES or "_static" in rel.parts:
            continue
        if rel.as_posix() not in wheel_names:
            missing.append(rel.as_posix())
    assert missing == []


def test_wheel_has_no_modules_absent_from_src(wheel_names):
    stale = [
        n
        for n in wheel_names
        if n.startswith("orb/") and n.endswith(".py") and not (SRC / n).exists()
    ]
    assert stale == []
