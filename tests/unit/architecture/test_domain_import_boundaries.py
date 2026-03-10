"""Task 1739: Domain layer import boundary tests.

Asserts that no file under src/orb/domain/ imports from infrastructure,
providers, interface, api, or cli layers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.architecture.conftest import (
    SRC_ORB,
    collect_python_files,
    extract_imports,
)

_DOMAIN_DIR = SRC_ORB / "domain"

_FORBIDDEN_PREFIXES = (
    "orb.infrastructure",
    "orb.providers",
    "orb.interface",
    "orb.api",
    "orb.cli",
)

_DOMAIN_FILES = collect_python_files(_DOMAIN_DIR)


@pytest.mark.parametrize("filepath", _DOMAIN_FILES, ids=lambda p: str(p.relative_to(SRC_ORB)))
@pytest.mark.unit
@pytest.mark.architecture
def test_domain_file_has_no_forbidden_imports(filepath: Path) -> None:
    """Domain file must not import from outer layers."""
    imports = extract_imports(filepath)
    violations = [
        imp
        for imp in imports
        if any(imp == prefix or imp.startswith(prefix + ".") for prefix in _FORBIDDEN_PREFIXES)
    ]
    assert violations == [], (
        f"{filepath.relative_to(SRC_ORB)} imports from forbidden layers: {violations}"
    )
