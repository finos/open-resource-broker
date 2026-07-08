"""Tests for _resolve_static_dir in orb.ui.app.

We exercise the real ``_resolve_static_dir`` function by loading it from
source without triggering app.py's module-level side-effects (rx.App(...),
_initialize_orb_application_sync(), etc.).

Strategy
--------
``_resolve_static_dir`` does only function-level imports:

    from reflex.utils import prerequisites
    from reflex_base import constants

and then evaluates::

    web_bundle = (prerequisites.get_web_dir() / constants.Dirs.STATIC).resolve()
    if (web_bundle / "index.html").is_file():
        return web_bundle
    packaged = (Path(__file__).parent / "_static").resolve()
    if (packaged / "index.html").is_file():
        return packaged
    return None

We isolate and call this function by:
  1. Loading only the function object from the source file via
     ``compile`` + ``exec`` into a fresh namespace.
  2. Patching ``sys.modules`` for the two library imports.
  3. Faking ``__file__`` in the namespace so ``Path(__file__)`` resolves
     to ``packaged_dir.parent / "app.py"``.

This avoids importing the full app.py module which has module-level
``rx.App(...)`` calls that the test stub cannot satisfy.

Cases:
  (a) .web/build/client/index.html exists → returns that dir
  (b) only _static/index.html exists      → returns the _static dir
  (c) neither exists                      → returns None
  (d) web_bundle exists but lacks index.html → falls through to packaged
"""

from __future__ import annotations

import ast
import textwrap
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

# conftest.py installs the rx stub (no orb.ui.app imports at module scope).


# ---------------------------------------------------------------------------
# Loader: extract _resolve_static_dir source and build a callable
# ---------------------------------------------------------------------------

_APP_PY = Path(__file__).parent.parent.parent / "src" / "orb" / "ui" / "app.py"


def _extract_function_source(path: Path, fn_name: str) -> str:
    """Return the source of ``fn_name`` from *path* as a standalone function."""
    source = path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            lines = source.splitlines()
            fn_lines = lines[node.lineno - 1 : node.end_lineno]
            return textwrap.dedent("\n".join(fn_lines))
    raise ValueError(f"Function {fn_name!r} not found in {path}")


def _build_resolve_fn(fake_file: str) -> types.FunctionType:
    """Compile _resolve_static_dir into a namespace with a fake __file__."""
    fn_src = _extract_function_source(_APP_PY, "_resolve_static_dir")
    # We need the from __future__ import and pathlib available
    wrapper = f"from __future__ import annotations\nfrom pathlib import Path\n{fn_src}"
    ns: dict = {"__file__": fake_file}
    exec(compile(wrapper, "<test>", "exec"), ns)
    return ns["_resolve_static_dir"]


# ---------------------------------------------------------------------------
# Helper: call _resolve_static_dir with both lookup paths redirected
# ---------------------------------------------------------------------------


def _call_real_resolve(*, web_bundle_path: Path, packaged_dir: Path) -> Path | None:
    """Call _resolve_static_dir with patched internals.

    - ``prerequisites.get_web_dir()`` → ``web_bundle_path.parent``
    - ``constants.Dirs.STATIC``       → ``web_bundle_path.name``
    - ``Path(__file__).parent``       → ``packaged_dir.parent``
      (so ``/ "_static"`` resolves to ``packaged_dir``)
    """
    fake_file = str(packaged_dir.parent / "app.py")

    fake_prerequisites = MagicMock()
    fake_prerequisites.get_web_dir.return_value = web_bundle_path.parent

    fake_constants = MagicMock()
    fake_constants.Dirs.STATIC = web_bundle_path.name

    fake_reflex_utils = types.ModuleType("reflex.utils")
    fake_reflex_utils.prerequisites = fake_prerequisites  # type: ignore[attr-defined]

    fake_reflex_base = types.ModuleType("reflex_base")
    fake_reflex_base.constants = fake_constants  # type: ignore[attr-defined]

    with patch.dict(
        "sys.modules",
        {
            "reflex.utils": fake_reflex_utils,
            "reflex.utils.prerequisites": fake_prerequisites,
            "reflex_base": fake_reflex_base,
            "reflex_base.constants": fake_constants,
        },
    ):
        fn = _build_resolve_fn(fake_file)
        return fn()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResolveStaticDir:
    """Tests for the real _resolve_static_dir function with monkeypatched paths."""

    def test_web_bundle_takes_priority_when_index_html_exists(self, tmp_path: Path):
        """(a) .web/build/client/index.html exists → returns that dir."""
        web_bundle = tmp_path / ".web" / "build" / "client"
        web_bundle.mkdir(parents=True)
        (web_bundle / "index.html").write_text("<html/>")

        packaged = tmp_path / "_static"
        packaged.mkdir()
        (packaged / "index.html").write_text("<html/>")

        result = _call_real_resolve(web_bundle_path=web_bundle, packaged_dir=packaged)

        assert result == web_bundle.resolve()

    def test_packaged_static_returned_when_web_bundle_absent(self, tmp_path: Path):
        """(b) Only _static/index.html exists → returns _static dir."""
        web_bundle = tmp_path / ".web" / "build" / "client"
        web_bundle.mkdir(parents=True)
        # No index.html in web_bundle

        packaged = tmp_path / "_static"
        packaged.mkdir()
        (packaged / "index.html").write_text("<html/>")

        result = _call_real_resolve(web_bundle_path=web_bundle, packaged_dir=packaged)

        assert result == packaged.resolve()

    def test_returns_none_when_neither_exists(self, tmp_path: Path):
        """(c) Neither web_bundle nor _static has index.html → None."""
        web_bundle = tmp_path / ".web" / "build" / "client"
        web_bundle.mkdir(parents=True)

        packaged = tmp_path / "_static"
        packaged.mkdir()

        result = _call_real_resolve(web_bundle_path=web_bundle, packaged_dir=packaged)

        assert result is None

    def test_web_bundle_missing_index_html_falls_through_to_packaged(self, tmp_path: Path):
        """(d) web_bundle dir present but no index.html → falls back to packaged."""
        web_bundle = tmp_path / "bundle"
        web_bundle.mkdir()

        packaged = tmp_path / "_static"
        packaged.mkdir()
        (packaged / "index.html").write_text("<html/>")

        result = _call_real_resolve(web_bundle_path=web_bundle, packaged_dir=packaged)

        assert result == packaged.resolve()
