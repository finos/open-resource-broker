"""Unit tests for the ``orb server ui-export`` CLI handler."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orb.domain.base.exceptions import ValidationError


def _args(dest: str, force: bool = False) -> argparse.Namespace:
    return argparse.Namespace(dest=dest, force=force)


# ---------------------------------------------------------------------------
# Happy-path tests (unchanged)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_copies_bundle_to_dest(tmp_path: Path) -> None:
    """Handler copies the bundle to the destination and reports file count."""
    # Build a minimal fake static bundle in a source tmp dir.
    source = tmp_path / "static"
    source.mkdir()
    (source / "index.html").write_text("<html/>")
    (source / "assets").mkdir()
    (source / "assets" / "main.js").write_text("// js")

    dest = tmp_path / "export"

    from orb.interface.server_command_handlers import handle_server_ui_export

    with patch("orb.interface.server_command_handlers._ui_resolve_static_dir", return_value=source):
        result = await handle_server_ui_export(_args(str(dest)))

    assert result["status"] == "ok"
    assert result["dest"] == str(dest)
    assert result["file_count"] == 2
    assert (dest / "index.html").is_file()
    assert (dest / "assets" / "main.js").is_file()


@pytest.mark.asyncio
async def test_export_overwrites_non_empty_dest_with_force(tmp_path: Path) -> None:
    """Handler merges/overwrites into an existing destination when --force is set."""
    source = tmp_path / "static"
    source.mkdir()
    (source / "index.html").write_text("<html/>")

    dest = tmp_path / "existing"
    dest.mkdir()
    (dest / "stale.txt").write_text("old content")

    from orb.interface.server_command_handlers import handle_server_ui_export

    with patch("orb.interface.server_command_handlers._ui_resolve_static_dir", return_value=source):
        result = await handle_server_ui_export(_args(str(dest), force=True))

    assert result["status"] == "ok"
    assert (dest / "index.html").is_file()


# ---------------------------------------------------------------------------
# Failure paths — handler level (Finding 2, 4, 5)
# These verify the correct exception type and message content.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_raises_when_bundle_missing() -> None:
    """Handler raises ValidationError (non-zero exit) when the bundle is absent."""
    from orb.interface.server_command_handlers import handle_server_ui_export

    with patch("orb.interface.server_command_handlers._ui_resolve_static_dir", return_value=None):
        with pytest.raises(ValidationError) as exc_info:
            await handle_server_ui_export(_args("/tmp/irrelevant"))

    assert "bundle not found" in str(exc_info.value).lower()
    assert "pip install" in str(exc_info.value)


@pytest.mark.asyncio
async def test_export_raises_when_dest_non_empty_without_force(tmp_path: Path) -> None:
    """Handler raises ValidationError when dest is non-empty and --force is absent."""
    source = tmp_path / "static"
    source.mkdir()
    (source / "index.html").write_text("<html/>")

    dest = tmp_path / "existing"
    dest.mkdir()
    (dest / "stale.txt").write_text("old content")

    from orb.interface.server_command_handlers import handle_server_ui_export

    with patch("orb.interface.server_command_handlers._ui_resolve_static_dir", return_value=source):
        with pytest.raises(ValidationError) as exc_info:
            await handle_server_ui_export(_args(str(dest), force=False))

    assert "--force" in str(exc_info.value)
    # Original content must be untouched.
    assert (dest / "stale.txt").is_file()


@pytest.mark.asyncio
async def test_export_raises_when_dest_is_a_file(tmp_path: Path) -> None:
    """Finding 5 — dest points at an existing regular file; handler raises a clean error.

    Before the fix, ``dest.iterdir()`` raised ``NotADirectoryError`` and was
    surfaced as an opaque wrapped exception.  Now it's a ``ValidationError``
    with an actionable message.
    """
    source = tmp_path / "static"
    source.mkdir()
    (source / "index.html").write_text("<html/>")

    dest_file = tmp_path / "not-a-dir.txt"
    dest_file.write_text("i am a file")

    from orb.interface.server_command_handlers import handle_server_ui_export

    with patch("orb.interface.server_command_handlers._ui_resolve_static_dir", return_value=source):
        with pytest.raises(ValidationError) as exc_info:
            await handle_server_ui_export(_args(str(dest_file)))

    assert "not a directory" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_ui_resolve_static_dir_raises_when_extras_missing() -> None:
    """Finding 4 — missing UI extras give an actionable ValidationError, not a raw ImportError.

    We monkeypatch the import so the test does not require reflex to be absent.
    """
    import builtins

    real_import = builtins.__import__

    def _block_orb_ui(name, *args, **kwargs):
        if name == "orb.ui.app" or name.startswith("orb.ui."):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    # Ensure orb.ui.app is not cached in sys.modules so the lazy import fires.
    import sys

    cached = sys.modules.pop("orb.ui.app", None)
    try:
        with patch("builtins.__import__", side_effect=_block_orb_ui):
            from orb.interface.server_command_handlers import _ui_resolve_static_dir

            with pytest.raises(ValidationError) as exc_info:
                _ui_resolve_static_dir()
    finally:
        if cached is not None:
            sys.modules["orb.ui.app"] = cached

    assert "pip install" in str(exc_info.value)
    assert "orb-py[ui]" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Exit-code mapping tests (Finding 2)
# Drive failure paths through cli.router.execute_command so the real
# exit-code mapping (main.py:243-262 / formatter.format_error) is exercised.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bundle_missing_yields_nonzero_exit_via_router(tmp_path: Path) -> None:
    """Finding 2 — bundle-missing path must exit non-zero through the real dispatch path.

    We drive the failure through cli.router.execute_command, which re-raises
    the ValidationError.  Then we pass it through CLIResponseFormatter.format_error,
    mirroring the exact code path in cli.main:243-262.
    """
    from orb.cli.response_formatter import CLIResponseFormatter
    from orb.cli.router import execute_command

    args = argparse.Namespace(
        dest=str(tmp_path / "out"),
        force=False,
        resource="server",
        action="ui-export",
        format="json",
        input_data=None,
    )

    mock_container = MagicMock()
    formatter = CLIResponseFormatter()

    with (
        patch("orb.interface.server_command_handlers._ui_resolve_static_dir", return_value=None),
        patch("orb.infrastructure.di.container.get_container", return_value=mock_container),
        patch("orb.cli.registry.build_registry"),
        patch("orb.cli.registry.lookup") as mock_lookup,
    ):
        # Wire lookup to the real handler so the exception actually comes from
        # handle_server_ui_export (not a random mock).
        from orb.interface.server_command_handlers import handle_server_ui_export

        mock_lookup.return_value = handle_server_ui_export

        with pytest.raises(ValidationError) as exc_info:
            await execute_command(args, None, {})

    _output, exit_code = formatter.format_error(exc_info.value, "json")
    assert exit_code != 0, "bundle-missing path must produce a non-zero exit code"


@pytest.mark.asyncio
async def test_non_empty_dest_yields_nonzero_exit_via_router(tmp_path: Path) -> None:
    """Finding 2 — non-empty-dest-without-force path must exit non-zero through the real dispatch path."""
    source = tmp_path / "static"
    source.mkdir()
    (source / "index.html").write_text("<html/>")

    dest = tmp_path / "existing"
    dest.mkdir()
    (dest / "stale.txt").write_text("old content")

    from orb.cli.response_formatter import CLIResponseFormatter
    from orb.cli.router import execute_command

    args = argparse.Namespace(
        dest=str(dest),
        force=False,
        resource="server",
        action="ui-export",
        format="json",
        input_data=None,
    )

    mock_container = MagicMock()
    formatter = CLIResponseFormatter()

    with (
        patch("orb.interface.server_command_handlers._ui_resolve_static_dir", return_value=source),
        patch("orb.infrastructure.di.container.get_container", return_value=mock_container),
        patch("orb.cli.registry.build_registry"),
        patch("orb.cli.registry.lookup") as mock_lookup,
    ):
        from orb.interface.server_command_handlers import handle_server_ui_export

        mock_lookup.return_value = handle_server_ui_export

        with pytest.raises(ValidationError) as exc_info:
            await execute_command(args, None, {})

    _output, exit_code = formatter.format_error(exc_info.value, "json")
    assert exit_code != 0, "non-empty-dest path must produce a non-zero exit code"
