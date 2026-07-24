"""Regression test: ``make help`` must surface the provider test targets.

The help parser groups targets by ``# @SECTION`` markers and only prints
targets that appear under a section.  ``makefiles/providers.mk`` had no
``# @SECTION`` header and its per-provider targets are generated via
``$(eval $(call ...))`` — so ``print-providers`` and the
``test-providers-<name>-{unit,mocked,contract,live}`` targets never appeared
in ``make help``.

This test runs the real ``make help`` and asserts the provider targets are
now visible.  It skips gracefully when ``make`` (or its config prerequisites)
are unavailable in the environment.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = three levels up from tests/unit/<this file>.
_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("make") is None, reason="make not available")
def test_make_help_lists_provider_targets() -> None:
    if not (_REPO_ROOT / "Makefile").exists():
        pytest.skip("Makefile not present in this checkout")

    result = subprocess.run(
        ["make", "help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        pytest.skip(f"make help unavailable in this environment: {result.stderr[:200]}")

    out = result.stdout

    # The section header and the auto-discovery target must be present.
    assert "Provider Tests:" in out
    assert "print-providers" in out

    # The generated per-provider pattern targets are rendered with a <name>
    # placeholder (they are created dynamically for each discovered provider).
    assert "test-providers-<name>-live" in out
    assert "test-providers-<name>-unit" in out
