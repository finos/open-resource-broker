"""Keep CI tool installs hash-locked.

Tools that run in jobs holding security-events or id-token are installed from
``.github/requirements/*.txt`` with ``--require-hashes``. These tests fail if a
workflow installs a package unlocked (outside a short allow-list of installs
that are unlocked by design) or if a lock file loses its hashes or drifts from
its ``.in`` source.
"""

import fnmatch
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GITHUB = ROOT / ".github"
REQUIREMENTS = GITHUB / "requirements"

# Installs that are unlocked by design: the wheel under test.
ALLOWED_UNLOCKED = (
    "${WHEEL}",
    "find dist",
)
INSTALL_COMMAND = re.compile(r"^((uv|python3?\s+-m)\s+)?pip\s+install\b")
PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s\;]+)", re.MULTILINE)
REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)", re.MULTILINE)


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _run_scripts() -> list[tuple[str, str]]:
    scripts = []
    files = [*(GITHUB / "workflows").glob("*.yml"), *(GITHUB / "actions").glob("*/action.yml")]
    for path in files:
        data = yaml.safe_load(path.read_text())
        jobs = data.get("jobs") or {"action": data.get("runs", {})}
        for job in jobs.values():
            for step in job.get("steps", []):
                if "run" in step:
                    scripts.append((path.name, step["run"]))
    return scripts


def _install_lines() -> list[tuple[str, str]]:
    lines = []
    for name, script in _run_scripts():
        for raw in script.splitlines():
            line = raw.strip()
            if INSTALL_COMMAND.match(line):
                lines.append((name, line))
    return lines


@pytest.mark.unit
@pytest.mark.parametrize(
    "line",
    ["pip install x", "uv pip install x", "python -m pip install x", "python3 -m pip install x"],
)
def test_install_detector_recognises_every_pip_form(line: str) -> None:
    assert INSTALL_COMMAND.match(line)


@pytest.mark.unit
def test_python_dash_m_pip_installs_are_scanned() -> None:
    commands = [line for _, line in _install_lines()]
    assert any(line.startswith("python -m pip install") for line in commands)


@pytest.mark.unit
def test_workflow_pip_installs_are_hash_locked() -> None:
    offenders = [
        f"{name}: {line}"
        for name, line in _install_lines()
        if "--require-hashes" not in line
        and not any(allowed in line for allowed in ALLOWED_UNLOCKED)
    ]
    assert not offenders, offenders


@pytest.mark.unit
def test_hash_locked_installs_use_requirements_files_without_dependency_resolution() -> None:
    locked = [(n, line) for n, line in _install_lines() if "--require-hashes" in line]
    assert {n for n, _ in locked} >= {
        "reusable-security.yml",
        "validate-workflows.yml",
        "security-code.yml",
    }
    for name, line in locked:
        assert "--no-deps" in line, f"{name}: {line}"
        assert re.search(r"-r \.github/requirements/[\w\-]+\.txt", line), f"{name}: {line}"
        assert (ROOT / re.search(r"-r (\S+)", line).group(1)).exists(), f"{name}: {line}"


@pytest.mark.unit
def test_no_pip_self_upgrade_in_package_testing() -> None:
    assert "install --upgrade pip" not in (GITHUB / "workflows" / "package-testing.yml").read_text()


@pytest.mark.unit
@pytest.mark.parametrize("lock", sorted(REQUIREMENTS.glob("*.txt")), ids=lambda p: p.name)
def test_every_locked_requirement_has_hashes(lock: Path) -> None:
    # Join continuation lines, then check each requirement entry.
    text = lock.read_text().replace("\\\n", " ")
    entries = [line for line in text.splitlines() if re.match(r"^[A-Za-z0-9_.\-]+==", line)]
    assert entries
    for entry in entries:
        assert "--hash=sha256:" in entry, entry


@pytest.mark.unit
@pytest.mark.parametrize("source", sorted(REQUIREMENTS.glob("*.in")), ids=lambda p: p.name)
def test_lock_agrees_with_source_pins(source: Path) -> None:
    lock = source.with_suffix(".txt")
    assert lock.exists(), f"missing lock for {source.name}; run dev-tools/ci/update_tool_locks.sh"
    locked = {_normalise(k): v for k, v in PIN.findall(lock.read_text())}
    lines = [line for line in source.read_text().splitlines() if not line.startswith("#")]
    requirements = REQUIREMENT.findall("\n".join(lines))
    assert requirements
    for package in requirements:
        assert _normalise(package) in locked, f"{lock.name} does not lock {package}"
    for package, version in PIN.findall(source.read_text()):
        assert locked.get(_normalise(package)) == version, (
            f"{lock.name} does not pin {package}=={version}"
        )


@pytest.mark.unit
def test_pip_licenses_lock_includes_transitive_dependencies() -> None:
    locked = dict(PIN.findall((REQUIREMENTS / "pip-licenses.txt").read_text()))
    assert {"pip-licenses", "prettytable", "wcwidth"} <= set(locked)


def _workflow_path_filters(name: str) -> tuple[list[str], list[str]]:
    """Return the push ``paths`` and the pull-request ``paths-filter`` patterns."""
    path = GITHUB / "workflows" / name
    data = yaml.safe_load(path.read_text())
    triggers = data.get(True) or data["on"]
    text = path.read_text().split("filters:")[1]
    return triggers["push"]["paths"], re.findall(r"^\s+- '([^']+)'", text, re.MULTILINE)


def _covered(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


@pytest.mark.unit
def test_hashed_tools_run_on_requirements_changes() -> None:
    for name in ("validate-workflows.yml", "security-code.yml"):
        data = yaml.safe_load((GITHUB / "workflows" / name).read_text())
        triggers = data.get(True) or data["on"]
        assert ".github/requirements/**" in triggers["push"]["paths"], name
    # security-code.yml's pull_request path filter lives in the `changes` job.
    assert (
        ".github/requirements/**"
        in (GITHUB / "workflows" / "security-code.yml").read_text().split("filters:")[1]
    )


@pytest.mark.unit
def test_semgrep_project_changes_run_the_security_scan() -> None:
    push, pull_request = _workflow_path_filters("security-code.yml")
    for path in ("dev-tools/ci/semgrep/uv.lock", "dev-tools/ci/semgrep/pyproject.toml"):
        assert _covered(path, push), path
        assert _covered(path, pull_request), path


# Dependabot's uv ecosystem re-runs `uv pip compile` for every requirements file
# it finds in an update directory and in the directory's immediate subdirectories.
# It takes --universal, --generate-hashes and --python-version from the lock
# header and does not replay any other option.
REPLAYED_OPTIONS = ("--universal", "--generate-hashes", "--python-version")
NOT_REPLAYED_OPTIONS = ("--override", "--constraint", "--build-constraint", " -c ")
COMPILE_HEADER = re.compile(r"^#\s+uv pip compile (?P<args>.+)$", re.MULTILINE)


def _dependabot_requirement_dirs() -> list[Path]:
    config = yaml.safe_load((GITHUB / "dependabot.yml").read_text())
    dirs = []
    for update in config["updates"]:
        if update["package-ecosystem"] not in ("pip", "uv"):
            continue
        base = ROOT / update["directory"].lstrip("/")
        dirs.append(base)
        dirs.extend(sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")))
    return dirs


def _compiled_locks() -> list[Path]:
    return sorted(
        lock
        for directory in _dependabot_requirement_dirs()
        for lock in directory.glob("*.txt")
        if COMPILE_HEADER.search(lock.read_text())
    )


def _header_args(lock: Path) -> str:
    return COMPILE_HEADER.search(lock.read_text()).group("args")  # type: ignore[union-attr]


def _lock_id(lock: Path) -> str:
    return str(lock.relative_to(ROOT))


def _require_uv() -> str:
    uv = shutil.which("uv")
    assert uv, "uv is required for the Dependabot replay checks; install uv"
    return uv


def _lock_lines(text: str) -> list[str]:
    """Requirement and hash lines of a lock, without comments."""
    return [
        line.rstrip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


@pytest.mark.unit
def test_compiled_locks_are_found() -> None:
    names = {p.name for p in _compiled_locks()}
    assert {"build-tools.txt", "requirements.txt"} <= names


@pytest.mark.unit
@pytest.mark.parametrize("lock", _compiled_locks(), ids=_lock_id)
def test_compiled_lock_header_only_uses_options_dependabot_replays(lock: Path) -> None:
    args = f" {_header_args(lock)} "
    for option in REPLAYED_OPTIONS:
        assert option in args, f"{_lock_id(lock)} header lacks {option}"
    for option in NOT_REPLAYED_OPTIONS:
        assert option not in args, (
            f"{_lock_id(lock)} uses {option.strip()}, which Dependabot cannot replay; "
            "make the project a native uv project (pyproject.toml and uv.lock)"
        )


@pytest.mark.unit
@pytest.mark.parametrize("directory", _dependabot_requirement_dirs(), ids=_lock_id)
def test_dependabot_requirement_dirs_have_no_override_files(directory: Path) -> None:
    assert not list(directory.glob("*.override")), (
        f"{_lock_id(directory)} has an override file; Dependabot cannot replay --override"
    )


@pytest.mark.unit
@pytest.mark.parametrize("lock", _compiled_locks(), ids=_lock_id)
def test_dependabot_replay_reproduces_the_lock(lock: Path, tmp_path: Path) -> None:
    """Re-run the compile as Dependabot does and expect the committed lock back.

    The scratch directory is outside the repository so the root ``[tool.uv]``
    settings cannot leak in. The committed lock seeds the output file, so its
    pins are preferences and a newer upstream release does not fail the test.
    A lock that cannot resolve at its own --python-version fails here.
    """
    uv = _require_uv()
    args = _header_args(lock).split()
    source = args[args.index(next(a for a in args if a.endswith(".in")))]
    python_version = args[args.index("--python-version") + 1]
    shutil.copy(lock.parent / source, tmp_path / source)
    shutil.copy(lock, tmp_path / lock.name)
    result = subprocess.run(
        [
            uv,
            "pip",
            "compile",
            "--quiet",
            "--universal",
            "--generate-hashes",
            "--python-version",
            python_version,
            source,
            "-o",
            lock.name,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{_lock_id(lock)} does not resolve:\n{result.stderr}"
    assert _lock_lines((tmp_path / lock.name).read_text()) == _lock_lines(lock.read_text()), (
        f"{_lock_id(lock)} differs from a Dependabot-style recompile; "
        "run dev-tools/ci/update_tool_locks.sh"
    )


@pytest.mark.unit
def test_dependabot_tracks_requirements_directory() -> None:
    config = yaml.safe_load((GITHUB / "dependabot.yml").read_text())
    ecosystems = {u.get("directory"): u["package-ecosystem"] for u in config["updates"]}
    for directory in (
        "/.github/requirements",
        "/tests/e2e/microvm/worker",
        "/dev-tools/ci/semgrep",
    ):
        assert ecosystems.get(directory) == "uv", directory


SEMGREP_PROJECT = ROOT / "dev-tools" / "ci" / "semgrep"
SEMGREP_OVERRIDES = (
    "mcp",
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-http",
    "opentelemetry-instrumentation-requests",
    "opentelemetry-instrumentation-threading",
    "wcmatch",
)


@pytest.mark.unit
def test_semgrep_project_lock_keeps_its_overrides() -> None:
    lock = (SEMGREP_PROJECT / "uv.lock").read_text()
    manifest = lock.split("[manifest]")[1].split("[[package]]")[0]
    for name in SEMGREP_OVERRIDES:
        assert f'name = "{name}"' in manifest, name
    assert 'name = "semgrep"' in lock


@pytest.mark.unit
def test_semgrep_has_no_requirements_file_left_behind() -> None:
    assert not list(REQUIREMENTS.glob("semgrep.*"))


@pytest.mark.unit
def test_semgrep_installs_from_its_lock_with_the_workflow_interpreter() -> None:
    data = yaml.safe_load((GITHUB / "workflows" / "reusable-security.yml").read_text())
    steps = data["jobs"]["security-scan"]["steps"]
    install = next(s for s in steps if s.get("name") == "Install Semgrep")
    assert "uv sync --locked" in install["run"]
    assert "--frozen" not in install["run"]
    assert "--python" in install["run"]
    assert install["env"]["UV_PYTHON_DOWNLOADS"] == "never"
    assert any(s.get("id") == "semgrep-python" for s in steps)
    text = (GITHUB / "workflows" / "reusable-security.yml").read_text()
    assert "requirements/semgrep" not in text
    assert "pip install semgrep" not in text


@pytest.mark.unit
def test_semgrep_upgrade_is_compared_with_the_base_commit() -> None:
    data = yaml.safe_load((GITHUB / "workflows" / "reusable-security.yml").read_text())
    steps = data["jobs"]["security-scan"]["steps"]
    canary = next(
        s for s in steps if s.get("name") == "Compare Semgrep results with the base commit"
    )
    assert "semgrep_baseline_check.py" in canary["run"]
    assert "uv sync --locked" in canary["run"]
    assert (ROOT / "dev-tools" / "ci" / "semgrep_baseline_check.py").exists()


CSHARP_LOCK_GUARD = ROOT / "dev-tools" / "ci" / "check_csharp_lock_selftest.sh"


@pytest.mark.unit
def test_csharp_lock_guard_accepts_and_rejects_the_right_locks() -> None:
    """Run the guard's own accept/reject cases against the committed lock."""
    assert shutil.which("jq"), "jq is required for the C# lock guard test"
    result = subprocess.run(
        [str(CSHARP_LOCK_GUARD), "--no-restore"], capture_output=True, text=True, cwd=ROOT
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.unit
def test_csharp_restore_is_not_locked_and_ci_runs_the_guard() -> None:
    csproj = ROOT / "sdk/csharp/src/FINOS.OpenResourceBroker/FINOS.OpenResourceBroker.csproj"
    assert "<RestoreLockedMode" not in csproj.read_text()
    assert "<RestorePackagesWithLockFile>true<" in csproj.read_text()
    makefile = (ROOT / "makefiles" / "sdk.mk").read_text()
    assert "sdk-csharp-check-lock:" in makefile
    assert (
        "sdk-csharp-check-lock"
        in makefile.split("sdk-csharp-check-drift:")[1].split("sdk-check-drift:")[0]
    )
    assert "check_csharp_lock_selftest.sh" in (GITHUB / "workflows" / "sdk.yml").read_text()
