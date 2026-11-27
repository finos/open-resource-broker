"""A template row takes its region/location from the provider instance.

Azure templates require ``location`` and GCP templates ``region``; the runtime
fills them from the instance configuration, and default resolution does the
same so that validation and loading agree.  Real Azure and GCP registrations
are used.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.application.dto.queries import ValidateTemplateQuery
from orb.application.queries.template_query_handlers import ValidateTemplateHandler
from orb.application.services.template_defaults_service import TemplateDefaultsService
from orb.infrastructure.adapters.template_configuration_adapter import TemplateConfigurationAdapter
from orb.infrastructure.registry.template_extension_registry import (
    TemplateExtensionRegistry,
    TemplateExtensionRegistryAdapter,
)
from tests.providers.contract.fresh_registry import freshly_registered_providers

GCP_CONFIG = {"project_id": "orb-example-12345", "zones": ["us-central1-a"]}


def _instance(name, type_, config=None, enabled=True, template_defaults=None):
    return SimpleNamespace(
        name=name,
        type=type_,
        enabled=enabled,
        config=config or {},
        template_defaults=template_defaults,
    )


@pytest.fixture
def registry():
    from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import FieldMappingRegistry
    from orb.providers.aws.registration import register_aws_extensions
    from orb.providers.azure.registration import register_azure_extensions
    from orb.providers.azure.scheduler.hostfactory_field_mapping import AzureFieldMapping
    from orb.providers.gcp.registration import register_gcp_extensions
    from orb.providers.gcp.scheduler.hostfactory_field_mapping import GCPFieldMapping
    from orb.providers.k8s.registration import register_k8s_extensions

    saved = FieldMappingRegistry.all()
    saved_extensions = dict(TemplateExtensionRegistry._extensions)
    FieldMappingRegistry.register("azure", AzureFieldMapping())
    FieldMappingRegistry.register("gcp", GCPFieldMapping())
    try:
        with freshly_registered_providers() as populated:
            # Provider initialisation registers these in the application.
            for register in (
                register_aws_extensions,
                register_azure_extensions,
                register_gcp_extensions,
                register_k8s_extensions,
            ):
                register()
            yield populated
    finally:
        TemplateExtensionRegistry._extensions.clear()
        TemplateExtensionRegistry._extensions.update(saved_extensions)
        FieldMappingRegistry.clear()
        for key, value in saved.items():
            FieldMappingRegistry.register(key, value)


def _service(registry, instances, provider_defaults=None) -> TemplateDefaultsService:
    manager = MagicMock()
    manager.get_template_config.return_value = {}
    manager.get_provider_config.return_value = SimpleNamespace(
        providers=instances,
        provider_defaults=provider_defaults or {},
        get_active_providers=lambda: [i for i in instances if i.enabled],
    )
    return TemplateDefaultsService(
        config_manager=manager,
        logger=MagicMock(),
        extension_registry=TemplateExtensionRegistryAdapter(),
        provider_registry=registry,
    )


def _azure_row(**extra) -> dict:
    return {
        "template_id": "t1",
        "image_id": "img",
        "provider_api": "VMSS",
        "provider_type": "azure",
        **extra,
    }


def _gcp_row(**extra) -> dict:
    return {
        "template_id": "t1",
        "image_id": "img",
        "provider_api": "MIG",
        "provider_type": "gcp",
        "machine_type": "n2-standard-2",
        "project_id": "orb-example-12345",
        "zones": ["us-central1-a", "us-central1-b"],
        **extra,
    }


def _validate(registry, instances, row) -> list[str]:
    """Run a file template through ValidateTemplateHandler with real defaults and adapter."""
    service = _service(registry, instances)
    manager = MagicMock()
    manager._registry = registry
    manager.config_manager = service.config_manager
    adapter = TemplateConfigurationAdapter(template_manager=manager, logger=MagicMock())
    handler = ValidateTemplateHandler(
        logger=MagicMock(),
        template_manager=adapter,
        error_handler=MagicMock(),
        template_defaults_service=service,
    )
    result = asyncio.run(handler.execute_query(ValidateTemplateQuery(template_config=row)))
    return result.get("validation_errors") or []


def _mentions(errors: list[str], field: str) -> bool:
    return any(field in e for e in errors)


# --- validate path ---------------------------------------------------------


def test_azure_validate_takes_location_from_the_sole_instance(registry) -> None:
    errors = _validate(
        registry, [_instance("azure-1", "azure", {"region": "eastus"})], _azure_row()
    )
    assert not _mentions(errors, "location")


def test_gcp_validate_takes_region_from_the_sole_instance(registry) -> None:
    instance = _instance("gcp-1", "gcp", {**GCP_CONFIG, "region": "us-central1"})
    errors = _validate(registry, [instance], _gcp_row())
    assert not _mentions(errors, "region")


def test_azure_validate_still_reports_location_when_the_instance_has_none(registry) -> None:
    errors = _validate(registry, [_instance("azure-1", "azure")], _azure_row())
    assert _mentions(errors, "location")


def test_gcp_validate_still_reports_region_when_the_instance_has_none(registry) -> None:
    errors = _validate(registry, [_instance("gcp-1", "gcp", dict(GCP_CONFIG))], _gcp_row())
    assert _mentions(errors, "region")


# --- resolution ------------------------------------------------------------


def test_azure_instance_with_raw_location_key_seeds_location(registry) -> None:
    service = _service(registry, [_instance("azure-1", "azure", {"location": "westus2"})])
    assert service.resolve_template_defaults(_azure_row())["location"] == "westus2"


@pytest.mark.parametrize(
    ("type_", "row"),
    [("aws", {"provider_type": "aws"}), ("k8s", {"provider_type": "k8s"})],
)
def test_providers_without_a_location_field_get_no_seed(registry, type_, row) -> None:
    service = _service(registry, [_instance(f"{type_}-1", type_, {"region": "eu-west-1"})])
    resolved = service.resolve_template_defaults({"template_id": "t1", **row})
    assert "region" not in resolved
    assert "location" not in resolved


def test_unnamed_row_gets_no_other_instance_defaults(registry) -> None:
    instance = _instance(
        "azure-1",
        "azure",
        {"region": "eastus"},
        template_defaults={"max_number": 99, "provider_api": "SingleVM"},
    )
    resolved = _service(registry, [instance]).resolve_template_defaults(_azure_row())
    assert resolved["location"] == "eastus"
    assert resolved["provider_api"] == "VMSS"
    assert "max_number" not in resolved


def test_row_value_beats_the_seed(registry) -> None:
    service = _service(registry, [_instance("azure-1", "azure", {"region": "eastus"})])
    assert service.resolve_template_defaults(_azure_row(location="westus"))["location"] == "westus"


def test_row_region_alias_gets_no_conflicting_location(registry) -> None:
    service = _service(registry, [_instance("azure-1", "azure", {"region": "eastus"})])
    resolved = service.resolve_template_defaults(_azure_row(region="westus"))
    assert resolved["region"] == "westus"
    assert "location" not in resolved


@pytest.mark.parametrize("key", ["region", "location"])
def test_instance_template_defaults_beat_the_seed(registry, key) -> None:
    instance = _instance(
        "azure-1", "azure", {"region": "eastus"}, template_defaults={key: "northeurope"}
    )
    row = _azure_row(provider_name="azure-1")
    resolved = _service(registry, [instance]).resolve_template_defaults(row)
    assert resolved.get(key) == "northeurope"
    assert not ({"region", "location"} - {key}) & set(resolved)


@pytest.mark.parametrize("key", ["region", "location"])
def test_provider_type_defaults_beat_the_seed(registry, key) -> None:
    defaults = {"azure": SimpleNamespace(template_defaults={key: "northeurope"})}
    service = _service(
        registry, [_instance("azure-1", "azure", {"region": "eastus"})], provider_defaults=defaults
    )
    resolved = service.resolve_template_defaults(_azure_row())
    assert resolved.get(key) == "northeurope"
    assert not ({"region", "location"} - {key}) & set(resolved)


def test_none_default_does_not_count_as_present(registry) -> None:
    instance = _instance(
        "azure-1", "azure", {"region": "eastus"}, template_defaults={"location": None}
    )
    resolved = _service(registry, [instance]).resolve_template_defaults(
        _azure_row(provider_name="azure-1")
    )
    assert resolved["location"] == "eastus"


@pytest.mark.parametrize(
    "instances",
    [
        [
            _instance("azure-1", "azure", {"region": "a"}),
            _instance("azure-2", "azure", {"region": "b"}),
        ],
        [_instance("azure-1", "azure", {"region": "a"}, enabled=False)],
    ],
    ids=["two-enabled", "only-disabled"],
)
def test_no_seed_without_exactly_one_enabled_instance(registry, instances) -> None:
    resolved = _service(registry, instances).resolve_template_defaults(_azure_row())
    assert "location" not in resolved


def test_untyped_row_is_not_seeded(registry) -> None:
    service = _service(registry, [_instance("azure-1", "azure", {"region": "eastus"})])
    row = _azure_row()
    del row["provider_type"]
    assert "location" not in service.resolve_template_defaults(row)


def test_selected_instance_of_another_type_is_never_the_source(registry) -> None:
    gcp = _instance("gcp-1", "gcp", {**GCP_CONFIG, "region": "us-central1"})
    azure = _instance("azure-1", "azure", {"region": "eastus"})
    resolved = _service(registry, [gcp, azure]).resolve_template_defaults(_azure_row(), "gcp-1")
    assert resolved["location"] == "eastus"
    assert "region" not in resolved


def test_no_seed_from_a_selected_instance_of_another_type_without_an_azure_one(registry) -> None:
    gcp = _instance("gcp-1", "gcp", {**GCP_CONFIG, "region": "us-central1"})
    resolved = _service(registry, [gcp]).resolve_template_defaults(_azure_row(), "gcp-1")
    assert "location" not in resolved
    assert "region" not in resolved


# --- provider contract -----------------------------------------------------


def test_extensions_declare_the_location_field_the_seed_relies_on(registry) -> None:
    fields = {
        provider: set(
            getattr(TemplateExtensionRegistry.get_extension_class(provider), "model_fields", {})
        )
        for provider in ("azure", "gcp", "aws", "k8s")
    }
    assert "location" in fields["azure"]
    assert "region" in fields["gcp"]
    for provider in ("aws", "k8s"):
        assert not fields[provider] & {"region", "location"}
