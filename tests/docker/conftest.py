"""Mark all docker tests as slow — they require Docker daemon and real builds."""

import pytest


def pytest_collection_modifyitems(items):
    """Mark all tests in the docker directory as slow."""
    for item in items:
        if "tests/docker" in str(item.fspath):
            item.add_marker(pytest.mark.slow)
