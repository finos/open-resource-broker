"""The SPA frontend cache key is scoped to the reflex version, not to uv.lock."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
KEY_PREFIX = "spa-web-"


def _steps(doc):
    """Yield steps of a workflow (jobs.*.steps) or a composite action (runs.steps)."""
    for job in (doc.get("jobs") or {}).values():
        yield from job.get("steps") or []
    yield from (doc.get("runs") or {}).get("steps") or []


def _spa_keys():
    files = [*(ROOT / ".github" / "workflows").glob("*.yml")]
    files += [*(ROOT / ".github" / "actions").glob("**/action.yml")]
    found = []
    for path in sorted(files):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        steps = list(_steps(doc))
        for step in steps:
            key = (step.get("with") or {}).get("key")
            if isinstance(key, str) and key.startswith(KEY_PREFIX):
                found.append((path, key, steps))
    return found


def test_spa_keys_use_the_reflex_version_not_uv_lock():
    found = _spa_keys()
    assert found, "no spa-web- cache keys found: the walk is not reaching the workflows"
    for path, key, steps in found:
        assert "uv.lock" not in key, f"{path.name}: key still hashes uv.lock"
        match = re.search(r"steps\.(\w+)\.outputs\.version", key)
        assert match, f"{path.name}: key does not use a version step output"
        step = next((s for s in steps if s.get("id") == match.group(1)), None)
        assert step is not None, f"{path.name}: no step with id {match.group(1)}"
        assert step["uses"] == "./.github/actions/reflex-version", path.name


def test_four_sites_define_the_key():
    assert len(_spa_keys()) == 4
