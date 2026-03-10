"""Task 1747: CLI-to-infrastructure direct import test.

Asserts that no file under src/orb/cli/ imports directly from orb.infrastructure
except through the DI container (which is an allowed entry point).

Known violations are whitelisted so tests pass today while catching NEW ones.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.architecture.conftest import (
    SRC_ORB,
    collect_python_files,
    extract_imports,
)

_CLI_DIR = SRC_ORB / "cli"
_CLI_FILES = collect_python_files(_CLI_DIR)

# Known violations — CLI files that currently import infrastructure directly.
# Remove entries as they are cleaned up.
_KNOWN_VIOLATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("cli/console.py", "orb.infrastructure.constants"),
        ("cli/registry.py", "orb.infrastructure.di.buses"),
        ("cli/registry.py", "orb.infrastructure.di.container"),
        ("cli/router.py", "orb.infrastructure.di.container"),
        ("cli/response_formatter.py", "orb.infrastructure.logging.logger"),
        ("cli/main.py", "orb.infrastructure.logging.logger"),
        ("cli/main.py", "orb.infrastructure.mocking.dry_run_context"),
        ("cli/main.py", "orb.infrastructure.di.container"),
        ("cli/factories/provider_command_factory.py", "orb.infrastructure.utilities.json_utils"),
    }
)


@pytest.mark.parametrize(
    "filepath", _CLI_FILES, ids=lambda p: str(p.relative_to(SRC_ORB))
)
@pytest.mark.unit
@pytest.mark.architecture
def test_cli_has_no_new_infrastructure_import(filepath: Path) -> None:
    """CLI file must not introduce NEW direct imports from orb.infrastructure."""
    rel = str(filepath.relative_to(SRC_ORB))
    imports = extract_imports(filepath)
    new_violations = [
        imp
        for imp in imports
        if (imp == "orb.infrastructure" or imp.startswith("orb.infrastructure."))
        and (rel, imp) not in _KNOWN_VIOLATIONS
    ]
    assert new_violations == [], (
        f"{rel} has NEW direct infrastructure imports — route through application layer "
        f"or DI ports instead: {new_violations}"
    )
