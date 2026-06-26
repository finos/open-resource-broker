"""End-to-end test configuration.

E2E tests use unittest-class ``setUp`` with ``tempfile.mkdtemp()`` and
several class-instance side effects that can race under xdist. Mark
every test in this tree serial; xdist runners can still pick them up,
but they will be scheduled sequentially via the ``serial`` marker.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):  # noqa: D401
    marker = pytest.mark.serial
    for item in items:
        item.add_marker(marker)
