"""Docker tests: slow + serial.

slow:    require a Docker daemon + real image builds.
serial:  bind a fixed host port (8003) and a fixed container name
         (orb-integration-test). Parallel workers would collide.
"""

import pytest


def pytest_collection_modifyitems(items):
    """Mark all tests in the docker directory as slow + serial."""
    for item in items:
        if "tests/docker" in str(item.fspath):
            item.add_marker(pytest.mark.slow)
            item.add_marker(pytest.mark.serial)
