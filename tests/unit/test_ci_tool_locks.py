"""Keep CI tool installs hash-locked.

Tools that run in jobs holding security-events or id-token are installed from
``.github/requirements/*.txt`` with ``--require-hashes``. These tests fail if a
workflow installs a package unlocked (outside a short allow-list of installs
that are unlocked by design) or if a lock file loses its hashes or drifts from
its ``.in`` source.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GITHUB = ROOT / ".github"
REQUIREMENTS = GITHUB / "requirements"

# Installs that are unlocked by design: the wheel under test, the test runner
# inside the wheel-test jobs, the wheel build front end, and the changelog tool.
ALLOWED_UNLOCKED = (
    "${WHEEL}",
    "find dist",
    "pip install build wheel",
    "pip install pytest pytest-mock",
    "git-changelog",
)
PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s\;]+)", re.MULTILINE)


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
            if re.match(r"^(uv )?pip install\b", line):
                lines.append((name, line))
    return lines


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
    locked = dict(PIN.findall(lock.read_text()))
    pins = PIN.findall(source.read_text())
    assert pins
    for package, version in pins:
        assert locked.get(package) == version, f"{lock.name} does not pin {package}=={version}"


@pytest.mark.unit
def test_pip_licenses_lock_includes_transitive_dependencies() -> None:
    locked = dict(PIN.findall((REQUIREMENTS / "pip-licenses.txt").read_text()))
    assert {"pip-licenses", "prettytable", "wcwidth"} <= set(locked)


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
def test_dependabot_tracks_requirements_directory() -> None:
    config = yaml.safe_load((GITHUB / "dependabot.yml").read_text())
    assert any(
        u["package-ecosystem"] == "pip" and u["directory"] == "/.github/requirements"
        for u in config["updates"]
    )
