"""Architecture test: providers/k8s and providers/aws must not import from each other.

Each provider tree is an independently deployable unit that can be installed
via its own extras dependency group (``[k8s]`` / ``[aws]``).  A cross-provider
import would couple the two extras together, forcing consumers to install both
even when they only need one.

Rules enforced:
  * No module under ``src/orb/providers/k8s/`` imports from ``orb.providers.aws``
  * No module under ``src/orb/providers/aws/`` imports from ``orb.providers.k8s``

Mirrors the SDK-confinement approach of ``test_boto3_leak_detection.py`` and
``test_k8s_leak_detection.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.architecture.conftest import (
    SRC_ORB,
    collect_python_files,
    extract_imports,
)

_PROVIDERS_AWS_DIR = SRC_ORB / "providers" / "aws"
_PROVIDERS_K8S_DIR = SRC_ORB / "providers" / "k8s"

_K8S_FILES = collect_python_files(_PROVIDERS_K8S_DIR)
_AWS_FILES = collect_python_files(_PROVIDERS_AWS_DIR)

# Known violations — should be empty; add entries only with a justification
# comment explaining why the cross-provider dependency is unavoidable.
_K8S_IMPORTING_AWS_VIOLATIONS: frozenset[tuple[str, str]] = frozenset()
_AWS_IMPORTING_K8S_VIOLATIONS: frozenset[tuple[str, str]] = frozenset()


@pytest.mark.parametrize(
    "filepath",
    _K8S_FILES,
    ids=lambda p: str(p.relative_to(SRC_ORB)),
)
@pytest.mark.unit
@pytest.mark.architecture
def test_k8s_does_not_import_aws(filepath: Path) -> None:
    """No module under providers/k8s/ may import from orb.providers.aws."""
    rel = str(filepath.relative_to(SRC_ORB))
    imports = extract_imports(filepath)
    new_violations = [
        imp
        for imp in imports
        if (imp == "orb.providers.aws" or imp.startswith("orb.providers.aws."))
        and (rel, imp) not in _K8S_IMPORTING_AWS_VIOLATIONS
    ]
    assert new_violations == [], (
        f"{rel} imports from orb.providers.aws — providers must be independently "
        f"installable; cross-provider imports couple the [k8s] and [aws] extras. "
        f"Violations: {new_violations}"
    )


@pytest.mark.parametrize(
    "filepath",
    _AWS_FILES,
    ids=lambda p: str(p.relative_to(SRC_ORB)),
)
@pytest.mark.unit
@pytest.mark.architecture
def test_aws_does_not_import_k8s(filepath: Path) -> None:
    """No module under providers/aws/ may import from orb.providers.k8s."""
    rel = str(filepath.relative_to(SRC_ORB))
    imports = extract_imports(filepath)
    new_violations = [
        imp
        for imp in imports
        if (imp == "orb.providers.k8s" or imp.startswith("orb.providers.k8s."))
        and (rel, imp) not in _AWS_IMPORTING_K8S_VIOLATIONS
    ]
    assert new_violations == [], (
        f"{rel} imports from orb.providers.k8s — providers must be independently "
        f"installable; cross-provider imports couple the [aws] and [k8s] extras. "
        f"Violations: {new_violations}"
    )
