"""Task 1741: Provider leak detection tests.

Asserts that no file OUTSIDE src/orb/providers/ imports from orb.providers.aws
or any other provider-specific module.

DI registration files are whitelisted as they must wire providers into the container.
Known violations in other layers are tracked so tests pass today while catching
any NEW leaks.
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

_PROVIDERS_DIR = SRC_ORB / "providers"

# All source files that live outside the providers package
_NON_PROVIDER_FILES = [
    f
    for f in collect_python_files(SRC_ORB)
    if not f.is_relative_to(_PROVIDERS_DIR) and str(f) not in EXCEPTION_PATHS
]

# Known violations — files that currently import from orb.providers but are not
# yet cleaned up.  Keyed as (relative_path, import_string).
_KNOWN_VIOLATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("bootstrap.py", "orb.providers.registry"),
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
        ("config/managers/provider_manager.py", "orb.providers.registry"),
        ("application/services/provider_registry_service.py", "orb.providers.registry"),
        ("infrastructure/template/configuration_manager.py", "orb.providers.base.strategy"),
        ("infrastructure/template/configuration_manager.py", "orb.providers.registry"),
        ("infrastructure/adapters/provider_discovery_adapter.py", "orb.providers.registry"),
        # DI wiring — intentional: storage registration must wire AWS provider
        ("infrastructure/storage/registration.py", "orb.providers.aws.storage.registration"),
        # hostfactory is inherently AWS/HPC-specific — provider import is expected
        (
            "infrastructure/scheduler/hostfactory/field_mapper.py",
            "orb.providers.aws.utilities.ec2.instances",
        ),
        (
            "infrastructure/scheduler/hostfactory/hostfactory_strategy.py",
            "orb.providers.aws.utilities.ec2.instances",
        ),
    }
)


@pytest.mark.parametrize("filepath", _NON_PROVIDER_FILES, ids=lambda p: str(p.relative_to(SRC_ORB)))
@pytest.mark.unit
@pytest.mark.architecture
def test_no_new_provider_leak(filepath: Path) -> None:
    """Non-provider file must not introduce NEW imports from orb.providers.*"""
    rel = str(filepath.relative_to(SRC_ORB))
    imports = extract_imports(filepath)
    new_violations = [
        imp
        for imp in imports
        if (imp == "orb.providers" or imp.startswith("orb.providers."))
        and (rel, imp) not in _KNOWN_VIOLATIONS
    ]
    assert new_violations == [], (
        f"{rel} has NEW provider leaks (not in known-violations whitelist): {new_violations}"
    )
