"""Every Reflex state class must be picklable once its computed vars are evaluated.

Reflex persists session state by pickling it; a computed var that returns a
closure or lambda makes the write queue fail with ``StateSerializationError``
and the pages stop loading. The check runs in a subprocess because the other
UI tests replace ``reflex`` with a stub.
"""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
import textwrap

import pytest

try:
    importlib.metadata.version("reflex")
    _HAS_REFLEX = True
except importlib.metadata.PackageNotFoundError:
    _HAS_REFLEX = False

_SCRIPT = textwrap.dedent(
    """
    import pickle
    import sys

    import orb.ui.app  # noqa: F401  registers every page and state
    from orb.ui.state import AppState
    from reflex.state import State

    schemas = {
        "aws": [
            {
                "key": "aws_" + kind,
                "path": "provider_data.x",
                "label": "X",
                "kind": kind,
                "resource_type": resource_type,
                "badge_color_map": {"a": "red"},
            }
            for resource_type in ("machines", "requests", "templates")
            for kind in ("text", "code", "badge", "count", "link")
        ]
    }

    def walk(state):
        yield state
        for child in state.substates.values():
            yield from walk(child)

    root = State(_reflex_internal_init=True)
    for state in walk(root):
        if isinstance(state, AppState):
            state.provider_schemas = schemas
    root.get_delta()

    failures = []
    for state in walk(root):
        try:
            pickle.dumps(state)
        except Exception as exc:
            failures.append(type(state).__name__ + ": " + str(exc))
    if failures:
        print("\\n".join(failures))
        sys.exit(1)
    """
)


@pytest.mark.skipif(not _HAS_REFLEX, reason="reflex (ui extra) not installed")
def test_all_ui_states_are_picklable() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
