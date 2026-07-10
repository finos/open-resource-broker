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
        ("bootstrap/core_services.py", "orb.providers.registry"),
        # DI wiring — intentional: core services registers ProviderMetricsPort
        ("bootstrap/core_services.py", "orb.providers.base.metrics"),
        ("bootstrap/infrastructure_services.py", "orb.providers.registration"),
        ("bootstrap/infrastructure_services.py", "orb.providers.k8s"),
        ("bootstrap/infrastructure_services.py", "orb.providers.k8s.registration"),
        # DI wiring — intentional: AMI resolver registration must wire AWS provider
        ("bootstrap/infrastructure_services.py", "orb.providers.aws.domain.services.ami_resolver"),
        (
            "application/services/orchestration/dashboard_summary.py",
            "orb.providers.registry",
        ),
        ("bootstrap/provider_services.py", "orb.providers.registry"),
        ("bootstrap/provider_services.py", "orb.providers.registration"),
        ("bootstrap/services.py", "orb.providers.registration"),
        # Provider completeness assertion — intentional: must query ProviderRegistry
        # and all satellite registries to verify every registered provider type is complete.
        ("bootstrap/provider_completeness.py", "orb.providers.registry"),
        (
            "bootstrap/provider_completeness.py",
            "orb.providers.registry.defaults_loader_registry",
        ),
        ("interface/health_command_handler.py", "orb.providers.registry"),
        ("interface/system_command_handlers.py", "orb.providers.registry"),
        ("interface/infrastructure_command_handler.py", "orb.providers.registry"),
        ("interface/provider_config_handler.py", "orb.providers.registry"),
        ("interface/provider_config_handler.py", "orb.providers.factory"),
        ("interface/init_command_handler.py", "orb.providers.registry"),
        ("interface/init_command_handler.py", "orb.providers.factory"),
        ("interface/machine_command_handlers.py", "orb.providers.base.strategy"),
        ("interface/mcp/server/core.py", "orb.providers.registry"),
        ("config/managers/provider_manager.py", "orb.providers.registry"),
        ("application/services/provider_registry_service.py", "orb.providers.registry"),
        ("infrastructure/template/configuration_manager.py", "orb.providers.base.strategy"),
        ("infrastructure/template/configuration_manager.py", "orb.providers.registry"),
        ("infrastructure/adapters/provider_discovery_adapter.py", "orb.providers.registry"),
        # ProviderCLISpecPort moved out of domain (E2) — registry uses the protocol type
        (
            "infrastructure/registry/cli_spec_registry.py",
            "orb.providers.base.provider_cli_spec_port",
        ),
        # DI wiring — intentional: storage registration must wire AWS provider
        ("infrastructure/storage/registration.py", "orb.providers.aws.storage.registration"),
        # The cpu/ram lookup (derive_cpu_ram_from_instance_type) has been moved
        # into providers/aws/scheduler/hostfactory_field_mapping.py.  The shared
        # hostfactory infrastructure no longer imports from orb.providers.aws.*.
        ("config/schemas/cleanup_schema.py", "orb.providers.aws.configuration.cleanup_config"),
        # loader collects strategy-contributed defaults at load time — intentional bootstrap wiring
        ("config/loader.py", "orb.providers.registry"),
        ("config/loader.py", "orb.providers"),
        ("config/loader.py", "orb.providers.registration"),
        # CLI spec bootstrap: build_parser triggers lightweight CLI-spec registration
        # so that provider flags (e.g. --aws-profile) are available before app init.
        ("cli/args.py", "orb.providers.registration"),
        # Provider schema endpoints: the UI column schema is a pure metadata read
        # with no side effects; the registry is queried read-only to enumerate
        # registered strategy classes and call get_ui_column_schema() on them.
        # TODO: extract to an application service when a suitable one exists.
        ("api/routers/providers.py", "orb.providers.registry.provider_registry"),
        ("api/routers/providers.py", "orb.providers.registry.types"),
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
