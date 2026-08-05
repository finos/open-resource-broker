"""Declare the intentionally empty Azure live-test suite.

Azure tests that exercise real cloud infrastructure are private.
CI requires every discovered provider has a live-suite boundary.
"""

from pathlib import Path

import pytest

_LIVE_TEST_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep current and future Azure live tests in the serial CI leg."""
    serial = pytest.mark.serial
    for item in items:
        if item.path.is_relative_to(_LIVE_TEST_DIR):
            item.add_marker(serial)


def pytest_collection_finish(session: pytest.Session) -> None:
    """Announce the intentional absence of Azure live tests."""
    if _is_direct_live_suite_run(session) and not session.items:
        terminal_reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if terminal_reporter is not None:
            terminal_reporter.write_line("Azure live tests: intentionally none")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Treat this suite's intentional lack of tests as a successful run."""
    if _is_direct_live_suite_run(session) and exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = pytest.ExitCode.OK


def _is_direct_live_suite_run(session: pytest.Session) -> bool:
    """Return whether pytest was explicitly pointed at this live directory."""
    return any(Path(argument).resolve() == _LIVE_TEST_DIR for argument in session.config.args)
