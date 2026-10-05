"""Architecture test: getattr() calls in a provider must be justified.

Provider SDKs (AWS boto3, the Azure SDK, the Google Cloud SDK, the
Kubernetes client, ...) expose heterogeneous, loosely-typed objects, which
makes ``getattr()`` an easy way to silently paper over a missing or renamed
attribute. ``dev-tools/quality/quality_check.py`` defines a
``GetattrJustificationChecker`` that requires a nearby ``# getattr: <reason>``
(or function-level ``# getattr throughout ...``) comment for every call
under a discovered provider package, plus a ``discover_provider_names()``
helper that finds every provider under ``src/orb/providers/`` without a
hard-coded list.

Azure and Google Cloud already carry a justification comment on every call,
so their ceiling is zero. AWS and Kubernetes carry a pre-existing backlog of
unjustified calls that is tracked separately; this test ratchets that
backlog down using the same ``violation_counts.json`` ceiling mechanism as
``test_violation_ratchet.py`` so the count can only shrink, never grow.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.unit.architecture.conftest import SRC_ORB, collect_python_files

_DEV_TOOLS_QUALITY_PATH = str(Path(__file__).parents[3] / "dev-tools" / "quality")
if _DEV_TOOLS_QUALITY_PATH not in sys.path:
    sys.path.insert(0, _DEV_TOOLS_QUALITY_PATH)

from quality_check import (  # type: ignore[import]  # noqa: E402
    GetattrJustificationChecker,
    discover_provider_names,
)

_CEILINGS_FILE = Path(__file__).parent / "violation_counts.json"
_PROVIDER_NAMES = discover_provider_names()

_checker = GetattrJustificationChecker()


def _load_ceiling(provider: str) -> int:
    """Return the stored ceiling for *provider*, defaulting to zero.

    A provider with no entry in violation_counts.json has never had a
    recorded backlog, so any unjustified getattr() call in it is a new
    violation rather than a ratcheted-down pre-existing one.
    """
    ceilings = json.loads(_CEILINGS_FILE.read_text(encoding="utf-8"))
    return ceilings.get(f"getattr_justification_{provider}", 0)


def _count_violations(provider: str) -> int:
    provider_dir = SRC_ORB / "providers" / provider
    count = 0
    for file_path in collect_python_files(provider_dir):
        count += len(_checker.check_file(str(file_path)))
    return count


@pytest.mark.parametrize("provider", _PROVIDER_NAMES)
@pytest.mark.unit
@pytest.mark.architecture
def test_getattr_violations_have_not_increased(provider: str) -> None:
    """Unjustified getattr() calls in *provider* must not exceed the ceiling."""
    ceiling = _load_ceiling(provider)
    current = _count_violations(provider)
    assert current <= ceiling, (
        f"getattr() justification ratchet breached for '{provider}': "
        f"current={current} > ceiling={ceiling}. Add a '# getattr: <reason>' "
        "justification comment to the new call(s), or lower the ceiling in "
        "violation_counts.json if this is an intentional backlog reduction."
    )
