"""Every test suite under ``tests/providers/`` must be collected by a CI leg.

CI runs ``tests/providers/<name>`` once per provider that owns a
``testconf.mk`` (``.github/workflows/shared-config.yml`` discovery plus the
``ci-tests-providers`` make target).  Directories without one, such as the
cross-provider ``contract/`` suites, are not matrix entries, so they only run
if another leg names them.  This test asks make which paths the unit leg
passes to pytest and fails when a shared directory that holds tests is missing.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROVIDERS_DIR = _REPO_ROOT / "tests" / "providers"


def _shared_suite_dirs() -> list[str]:
    """``tests/providers/<d>`` dirs that hold tests but are not provider matrix entries."""
    found = []
    for child in sorted(_PROVIDERS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith("__"):
            continue
        if (child / "testconf.mk").exists():
            continue
        if any(child.rglob("test_*.py")):
            found.append(child.relative_to(_REPO_ROOT).as_posix())
    return found


def _unit_leg_pytest_args() -> list[str]:
    result = subprocess.run(
        ["make", "-n", "ci-tests-unit"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(f"make unavailable in this environment: {result.stderr[:200]}")
    for line in result.stdout.splitlines():
        argv = shlex.split(line)
        if "pytest" in argv:
            return argv[argv.index("pytest") + 1 :]
    raise AssertionError(f"ci-tests-unit does not invoke pytest:\n{result.stdout}")


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("make") is None, reason="make not available")
def test_unit_leg_collects_shared_provider_suites() -> None:
    if not (_REPO_ROOT / "Makefile").exists():
        pytest.skip("Makefile not present in this checkout")

    shared = _shared_suite_dirs()
    assert "tests/providers/contract" in shared

    args = _unit_leg_pytest_args()
    missing = [d for d in shared if d not in args]
    assert not missing, (
        f"suites under tests/providers/ with no testconf.mk are never run by CI: {missing}; "
        "add them to TESTS_PROVIDERS_SHARED in makefiles/common.mk"
    )
