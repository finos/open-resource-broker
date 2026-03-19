"""Task 1740: Application layer import boundary tests.

Asserts that no file under src/orb/application/ imports from infrastructure,
providers, interface, api, or cli layers.

Known violations are tracked in violation_counts.json (ratchet).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.architecture.conftest import (
    SRC_ORB,
    collect_python_files,
    extract_imports,
)

_APP_DIR = SRC_ORB / "application"

_FORBIDDEN_PREFIXES = (
    "orb.infrastructure",
    "orb.providers",
    "orb.interface",
    "orb.api",
    "orb.cli",
)

# Known violations that exist in the current codebase — whitelisted so tests
# pass today while still catching any NEW violations introduced.
_KNOWN_VIOLATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("application/queries/template_query_handlers.py", "orb.infrastructure.template.dtos"),
        ("application/commands/template_handlers.py", "orb.infrastructure.template.dtos"),
        (
            "application/services/orchestration/refresh_templates.py",
            "orb.infrastructure.template.dtos",
        ),
        (
            "application/commands/cleanup_handlers.py",
            "orb.infrastructure.events.infrastructure_events",
        ),
        (
            "application/services/provisioning_orchestration_service.py",
            "orb.infrastructure.resilience.exceptions",
        ),
        (
            "application/services/provisioning_orchestration_service.py",
            "orb.infrastructure.resilience.strategy.circuit_breaker",
        ),
        ("application/services/provider_registry_service.py", "orb.providers.registry"),
    }
)

_APP_FILES = collect_python_files(_APP_DIR)


@pytest.mark.parametrize("filepath", _APP_FILES, ids=lambda p: str(p.relative_to(SRC_ORB)))
@pytest.mark.unit
@pytest.mark.architecture
def test_application_file_has_no_new_forbidden_imports(filepath: Path) -> None:
    """Application file must not introduce NEW imports from outer layers."""
    rel = str(filepath.relative_to(SRC_ORB))
    imports = extract_imports(filepath)
    new_violations = [
        imp
        for imp in imports
        if any(imp == prefix or imp.startswith(prefix + ".") for prefix in _FORBIDDEN_PREFIXES)
        and (rel, imp) not in _KNOWN_VIOLATIONS
    ]
    assert new_violations == [], (
        f"{rel} has NEW forbidden imports (not in known-violations whitelist): {new_violations}"
    )
