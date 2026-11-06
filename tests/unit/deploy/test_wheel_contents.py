"""The built wheel must carry every runtime data file and no stale modules."""

import importlib.util
import json
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
def built_wheel(tmp_path_factory) -> Path:
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
    return next(outdir.glob("orb_py-*.whl"))


@pytest.fixture(scope="module")
def wheel_names(built_wheel) -> set[str]:
    with zipfile.ZipFile(built_wheel) as zf:
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


def test_k8s_defaults_load_from_the_installed_wheel(built_wheel, tmp_path):
    """The k8s defaults loader finds its bundled file when only the wheel is importable.

    The loader swallows read errors and returns ``{}``, so a wheel that omits
    ``k8s_defaults.json`` surfaces only as a provider with no supported APIs.
    Import the loader from the unpacked wheel, not from the source tree, and
    require the Pod handler to be present.
    """
    site = tmp_path / "site"
    with zipfile.ZipFile(built_wheel) as zf:
        zf.extractall(site)

    probe = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import orb.providers.k8s.defaults_loader as loader\n"
        "from pathlib import Path\n"
        "assert Path(loader.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve()), "
        "loader.__file__\n"
        "defaults = loader.KubernetesDefaultsLoader().load_defaults()\n"
        "handlers = defaults['provider']['provider_defaults']['k8s']['handlers']\n"
        "print(json.dumps(sorted(handlers)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(site)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "Pod" in json.loads(result.stdout.strip().splitlines()[-1])
