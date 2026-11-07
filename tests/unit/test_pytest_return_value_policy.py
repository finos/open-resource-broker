"""The repository pytest configuration fails tests that return a value."""

import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

pytest_plugins = ["pytester"]

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _repo_filterwarnings() -> list[str]:
    with _PYPROJECT.open("rb") as f:
        return tomllib.load(f)["tool"]["pytest"]["ini_options"]["filterwarnings"]


def test_returning_a_value_fails_under_repo_filterwarnings(pytester):
    entries = ", ".join(json.dumps(entry) for entry in _repo_filterwarnings())
    pytester.makepyprojecttoml(f"[tool.pytest.ini_options]\nfilterwarnings = [{entries}]\n")
    pytester.makepyfile(
        """
        def test_returns_false():
            return False
        """
    )

    result = pytester.runpytest("-p", "no:cacheprovider")

    result.assert_outcomes(failed=1)
