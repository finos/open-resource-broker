"""Setuptools build hook shim.

All project metadata lives in pyproject.toml (build-backend =
"setuptools.build_meta").  This file exists solely to subclass build_py so the
Reflex SPA static bundle is compiled automatically whenever the wheel is built
with ``python -m build``.  The bundle is written to ``src/orb/ui/_static/``
*before* ``super().run()`` scans ``package_data``, so setuptools packages it
with no extra wiring.

Set ORB_SKIP_UI_BUILD=1 to skip the build (core-only wheels, CI jobs without
bun, fast iterative builds).  When skipped the wheel simply ships whatever is
already in ``src/orb/ui/_static/`` — nothing if it was never built — and the
runtime ``_resolve_static_dir()`` returns None cleanly.  A stale bundle left on
disk is the caller's to remove (``rm -rf src/orb/ui/_static``); a real build
wipes and regenerates it.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

_PROJECT_ROOT = pathlib.Path(__file__).parent.resolve()
_STATIC_INDEX = _PROJECT_ROOT / "src" / "orb" / "ui" / "_static" / "index.html"
_BUILD_SCRIPT = _PROJECT_ROOT / "dev-tools" / "package" / "build_ui.sh"


class build_py(_build_py):  # noqa: N801 — must match setuptools' command name
    """Compile the Reflex SPA bundle before packaging."""

    def run(self) -> None:
        if os.environ.get("ORB_SKIP_UI_BUILD", "").strip().lower() in ("1", "true", "yes"):
            print("ORB_SKIP_UI_BUILD set — not building the SPA bundle.", file=sys.stderr)
        elif _STATIC_INDEX.exists():
            print(f"SPA bundle already present at {_STATIC_INDEX.parent}.", file=sys.stderr)
        else:
            print("Building Reflex SPA static bundle...", file=sys.stderr)
            subprocess.run(["bash", str(_BUILD_SCRIPT), "--quiet"], check=True)
            if not _STATIC_INDEX.exists():
                raise SystemExit(f"UI build ran but {_STATIC_INDEX} is missing — see output above.")

        super().run()


setup(cmdclass={"build_py": build_py})
