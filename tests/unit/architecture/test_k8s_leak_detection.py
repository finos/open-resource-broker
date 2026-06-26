"""Architecture test: ``kubernetes`` must not be imported outside the k8s
provider tree.

Importing the ``kubernetes`` SDK directly in core or infrastructure modules
couples the entire application to the ``[k8s]`` (or ``[k8s-legacy]``) extra
even when neither is installed.  All ``kubernetes`` SDK usage must be
confined to:

* ``src/orb/providers/k8s/``  — modern provider (TYPE-CHECKED)
* ``src/orb/k8s_legacy/``     — legacy plugin (maintenance mode, filtered
  globally via ``EXCEPTION_PATHS``)

Mirrors the ``boto3``/``botocore`` confinement test in
``test_boto3_leak_detection.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.architecture.conftest import (
    EXCEPTION_PATHS,
    SRC_ORB,
    collect_python_files,
    extract_imports,
)

_PROVIDERS_K8S_DIR = SRC_ORB / "providers" / "k8s"

# All source files that live outside the k8s provider tree.  The legacy
# k8s plugin tree is already filtered globally via ``EXCEPTION_PATHS``.
_NON_K8S_FILES = [
    f
    for f in collect_python_files(SRC_ORB)
    if not f.is_relative_to(_PROVIDERS_K8S_DIR) and str(f) not in EXCEPTION_PATHS
]

# Top-level module names that constitute direct kubernetes SDK imports
_KUBERNETES_SDK_MODULES = frozenset({"kubernetes"})

# Known violations — files currently allowed to import kubernetes outside the
# provider/legacy trees.  Add entries only as a last resort with a tracking
# comment explaining why the cross-boundary import is justified.
_KNOWN_VIOLATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        # interface/cli/k8s_legacy.py performs an ImportError-guarded availability
        # probe for the [k8s-legacy] extra ("is the kubernetes SDK installed?").
        # The CLI shim sits between the user and orb.k8s_legacy.cli.* — it must
        # decide whether to print _INSTALL_HINT or hand off to the legacy click
        # entry point.  The import is guarded by try/except ImportError and the
        # imported module is not actually used (it is the availability sentinel).
        ("interface/cli/k8s_legacy.py", "kubernetes"),
    }
)


@pytest.mark.parametrize(
    "filepath",
    _NON_K8S_FILES,
    ids=lambda p: str(p.relative_to(SRC_ORB)),
)
@pytest.mark.unit
@pytest.mark.architecture
def test_no_kubernetes_outside_provider(filepath: Path) -> None:
    """``kubernetes`` imports must not appear outside the k8s provider trees."""
    rel = str(filepath.relative_to(SRC_ORB))
    imports = extract_imports(filepath)
    new_violations = [
        imp
        for imp in imports
        if (
            imp in _KUBERNETES_SDK_MODULES
            or any(imp.startswith(f"{m}.") for m in _KUBERNETES_SDK_MODULES)
        )
        and (rel, imp) not in _KNOWN_VIOLATIONS
    ]
    assert new_violations == [], (
        f"{rel} imports kubernetes outside providers/k8s/ or k8s_legacy/ — "
        f"move to providers/k8s/ or guard with try/except ImportError. "
        f"Violations: {new_violations}"
    )
