"""Task 1742: Scheduler leak detection tests.

Asserts that no file OUTSIDE src/orb/infrastructure/scheduler/ references
APScheduler internals (apscheduler, BackgroundScheduler, AsyncIOScheduler).

DI registration files are whitelisted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.architecture.conftest import (
    EXCEPTION_PATHS,
    SRC_ORB,
    collect_python_files,
    extract_imports,
)

_SCHEDULER_DIR = SRC_ORB / "infrastructure" / "scheduler"

_NON_SCHEDULER_FILES = [
    f
    for f in collect_python_files(SRC_ORB)
    if not f.is_relative_to(_SCHEDULER_DIR) and str(f) not in EXCEPTION_PATHS
]

# Module name fragments that indicate APScheduler internals leaking out
_SCHEDULER_FORBIDDEN = (
    "apscheduler",
    "APScheduler",
    "BackgroundScheduler",
    "AsyncIOScheduler",
)


@pytest.mark.parametrize(
    "filepath", _NON_SCHEDULER_FILES, ids=lambda p: str(p.relative_to(SRC_ORB))
)
@pytest.mark.unit
@pytest.mark.architecture
def test_no_scheduler_leak(filepath: Path) -> None:
    """Non-scheduler file must not import APScheduler internals."""
    rel = str(filepath.relative_to(SRC_ORB))
    imports = extract_imports(filepath)
    violations = [
        imp
        for imp in imports
        if any(kw.lower() in imp.lower() for kw in _SCHEDULER_FORBIDDEN)
    ]
    assert violations == [], (
        f"{rel} leaks scheduler internals: {violations}"
    )
