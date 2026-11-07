"""The TypeScript SDK drift check must type-check the SDK, not only bundle it.

``npm run build`` (tsup) strips types without checking them, so a type error in
``src/`` or in the generated client would otherwise pass CI. CI runs the
``sdk-typescript-check-drift`` make target after generating the client, so the
type-check belongs in that recipe.
"""

import re
from pathlib import Path

import pytest

SDK_MK = Path(__file__).resolve().parents[2] / "makefiles" / "sdk.mk"


def _recipe(target: str) -> list[str]:
    lines = SDK_MK.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if re.match(rf"{re.escape(target)}\s*:", line))
    recipe = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line.strip())
    return recipe


@pytest.mark.unit
def test_check_drift_runs_typecheck_after_build() -> None:
    commands = [c for c in _recipe("sdk-typescript-check-drift") if "npm" in c]
    assert commands
    steps = [part.strip() for part in commands[0].split("&&")]
    assert "npm run typecheck" in steps
    assert steps.index("npm run typecheck") > steps.index("npm run build")
