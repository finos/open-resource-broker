"""The server split modules must import without FastAPI or Starlette installed.

Each check runs in a fresh interpreter with the web frameworks blocked, so a
module-level ``fastapi``/``starlette`` import (directly or through
``orb.api.middleware``) in any of the split modules fails it, and so does a
change to where the "FastAPI not installed" error is raised.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.api]

_BLOCK_WEB_FRAMEWORKS = textwrap.dedent(
    """
    import importlib.abc, sys

    class _Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in {"fastapi", "starlette"}:
                raise ImportError(f"{name} blocked for this test")
            return None

    sys.meta_path.insert(0, _Block())
    """
)


def _run(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", _BLOCK_WEB_FRAMEWORKS + textwrap.dedent(body)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize(
    "module", ["orb.api.loopback_admin", "orb.api.middleware_stack", "orb.api.server"]
)
def test_module_imports_without_web_frameworks(module: str) -> None:
    result = _run(f"import importlib; importlib.import_module({module!r}); print('IMPORTED')")

    assert result.returncode == 0, result.stderr
    assert "IMPORTED" in result.stdout


def test_middleware_stack_does_not_import_the_middleware_package_at_module_level() -> None:
    result = _run(
        """
        import sys
        import orb.api.middleware_stack
        leaked = sorted(m for m in sys.modules if m.startswith("orb.api.middleware."))
        print("LEAKED", leaked)
        """
    )

    assert result.returncode == 0, result.stderr
    assert "LEAKED []" in result.stdout


def test_missing_fastapi_still_reports_the_install_hint_from_the_factory() -> None:
    result = _run(
        """
        from orb.api.server import create_fastapi_app
        try:
            create_fastapi_app(None)
        except ImportError as exc:
            print("MESSAGE", exc)
        """
    )

    assert result.returncode == 0, result.stderr
    assert "FastAPI not installed" in result.stdout
    assert "pip install orb-py[api]" in result.stdout
