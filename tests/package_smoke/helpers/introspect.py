"""Read facts from the installed package (``python -I introspect.py <what>``).

``commands``: every registered CLI (resource, action) pair.
``pages``: the routes of the UI app.
``version``: ``orb.__version__`` and the file ``orb`` was imported from.
"""

from __future__ import annotations

import json
import sys


def commands() -> list[list[str]]:
    from orb.cli import registry

    registry.build_registry()
    return sorted([list(key) for key in registry._REGISTRY])


def pages() -> dict[str, object]:
    from importlib.metadata import version as installed

    from orb.ui import app as ui_app

    routes = sorted(ui_app.app._unevaluated_pages)
    return {"routes": routes, "reflex": installed("reflex")}


def version() -> dict[str, str]:
    import orb

    return {"version": getattr(orb, "__version__", ""), "file": orb.__file__}


if __name__ == "__main__":
    print(json.dumps({"commands": commands, "pages": pages, "version": version}[sys.argv[1]]()))
