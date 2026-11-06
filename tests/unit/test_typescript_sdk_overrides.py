"""Guard the npm overrides of the TypeScript SDK.

minimatch below 11.0.1 has a published advisory. It reaches the SDK only
transitively through jest, and the parent ranges (``glob ^13`` and
``glob ^10``) cap it below the fix, so it is forced with an npm override. The
existing esbuild override must survive alongside it.
"""

import json
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parents[2] / "sdk" / "typescript"


def _version(v: str) -> tuple[int, ...]:
    return tuple(int(part) for part in v.split("-")[0].split("."))


@pytest.mark.unit
def test_every_locked_minimatch_is_patched() -> None:
    packages = json.loads((SDK / "package-lock.json").read_text())["packages"]
    minimatch = {
        k: v["version"] for k, v in packages.items() if k.split("node_modules/")[-1] == "minimatch"
    }
    assert minimatch
    for path, version in minimatch.items():
        assert _version(version) >= (11, 0, 1), f"{path} is {version}"


@pytest.mark.unit
def test_overrides_keep_esbuild_and_force_minimatch() -> None:
    overrides = json.loads((SDK / "package.json").read_text())["overrides"]
    assert "esbuild" in overrides
    assert overrides.get("minimatch") == "^11.0.1"
