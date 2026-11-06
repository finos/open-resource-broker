"""A provider whose optional extra is not installed must not clutter CLI output."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from orb.providers import registration as reg_module

# Blocks ``httpx`` the way an environment without the azure extra would, then
# hands over to the real CLI entry point.  The probe fails the run when the
# block is not effective, so a green result cannot come from a no-op hook.
_RUNNER = """
import sys

class _BlockHttpx:
    def find_spec(self, name, path=None, target=None):
        if name == "httpx" or name.startswith("httpx."):
            raise ImportError("No module named 'httpx'")
        return None

sys.meta_path.insert(0, _BlockHttpx())
try:
    import httpx  # noqa: F401
except ImportError:
    pass
else:
    sys.exit("httpx block is not effective")

from orb.run import cli_main

sys.exit(cli_main())
"""


def _run_orb(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    runner = tmp_path / "run_orb.py"
    runner.write_text(_RUNNER, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ORB_", "BRIDGE_", "PYTHON"))}
    env["HOME"] = str(home)
    env["ORB_CONFIG_DIR"] = str(tmp_path / "config")
    return subprocess.run(
        [sys.executable, "-I", str(runner), *args],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
        env=env,
        check=False,
    )


@pytest.mark.slow
def test_providers_list_without_httpx_is_clean(tmp_path):
    init = _run_orb(
        tmp_path,
        "init",
        "--non-interactive",
        "--provider-type",
        "aws",
        "--aws-region",
        "us-east-1",
        "--aws-profile",
        "default",
        "--config-dir",
        str(tmp_path / "config"),
        "--scripts-dir",
        str(tmp_path / "scripts"),
    )
    assert init.returncode == 0, init.stderr

    result = _run_orb(tmp_path, "providers", "list")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "Traceback" not in result.stdout


def test_missing_optional_dependency_is_one_line_with_traceback_at_debug(caplog):
    exc = ModuleNotFoundError("No module named 'httpx'")

    with caplog.at_level(logging.DEBUG):
        reg_module._provider_load_failure(
            "azure", exc, None, {"types": frozenset()}, optional_dependency=True
        )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].exc_info is None
    assert "azure" in warnings[0].getMessage()
    assert any(r.levelno == logging.DEBUG and r.exc_info for r in caplog.records)
