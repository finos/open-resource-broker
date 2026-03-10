"""Task 1745: Interface-to-provider direct import test.

Asserts that no file under src/orb/interface/, src/orb/api/, or src/orb/cli/
imports directly from orb.providers.

Interface/API/CLI layers must go through the application layer, never directly
to providers.  Known violations are whitelisted so tests pass today while
catching any NEW direct imports.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.architecture.conftest import (
    SRC_ORB,
    collect_python_files,
    extract_imports,
)

_INTERFACE_DIRS = [
    SRC_ORB / "interface",
    SRC_ORB / "api",
    SRC_ORB / "cli",
]

_INTERFACE_FILES = [
    f
    for d in _INTERFACE_DIRS
    for f in collect_python_files(d)
]

# Known violations — interface/api/cli files that currently import providers
# directly.  Remove entries as they are cleaned up.
_KNOWN_VIOLATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("interface/health_command_handler.py", "orb.providers.registry"),
        ("interface/system_command_handlers.py", "orb.providers.registry"),
        ("interface/infrastructure_command_handler.py", "orb.providers.registry"),
        ("interface/provider_config_handler.py", "orb.providers.registry"),
        ("interface/provider_config_handler.py", "orb.providers.factory"),
        ("interface/init_command_handler.py", "orb.providers.registry"),
        ("interface/init_command_handler.py", "orb.providers.factory"),
        ("interface/machine_command_handlers.py", "orb.providers.base.strategy"),
        ("interface/mcp/server/core.py", "orb.providers.registry"),
        ("api/server.py", "orb.providers.aws.auth.iam_strategy"),
        ("api/server.py", "orb.providers.aws.auth.cognito_strategy"),
    }
)


@pytest.mark.parametrize(
    "filepath", _INTERFACE_FILES, ids=lambda p: str(p.relative_to(SRC_ORB))
)
@pytest.mark.unit
@pytest.mark.architecture
def test_interface_has_no_new_provider_import(filepath: Path) -> None:
    """Interface/API/CLI file must not introduce NEW direct imports from orb.providers."""
    rel = str(filepath.relative_to(SRC_ORB))
    imports = extract_imports(filepath)
    new_violations = [
        imp
        for imp in imports
        if (imp == "orb.providers" or imp.startswith("orb.providers."))
        and (rel, imp) not in _KNOWN_VIOLATIONS
    ]
    assert new_violations == [], (
        f"{rel} has NEW direct provider imports — route through application layer instead: "
        f"{new_violations}"
    )
