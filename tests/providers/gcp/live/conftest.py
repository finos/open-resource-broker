"""Declare the intentionally empty GCP live-test suite."""

from pathlib import Path

import pytest

_LIVE_TEST_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep future GCP live tests in the serial CI leg."""
    serial = pytest.mark.serial
    for item in items:
        if item.path.is_relative_to(_LIVE_TEST_DIR):
            item.add_marker(serial)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Treat this suite's intentional lack of tests as a successful run."""
    if _is_direct_live_suite_run(session) and exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = pytest.ExitCode.OK


def _is_direct_live_suite_run(session: pytest.Session) -> bool:
    """Return whether pytest was explicitly pointed at this live directory."""
    return any(Path(argument).resolve() == _LIVE_TEST_DIR for argument in session.config.args)
