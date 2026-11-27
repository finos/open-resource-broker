"""Provider type resolution and validator configuration on the validate path.

Rows that carry no stamped provider type are resolved through the provider
configuration, so the Azure and GCP validators run for them.  The real registry
and the real Azure and GCP registrations are used.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.adapters.template_configuration_adapter import (
    TemplateConfigurationAdapter,
)
from tests.providers.contract.fresh_registry import freshly_registered_providers

GCP_CONFIG = {
    "project_id": "orb-example-12345",
    "region": "us-central1",
    "zones": ["us-central1-a"],
}


def _instance(name: str, type_: str, config: dict | None = None, enabled: bool = True):
    return SimpleNamespace(name=name, type=type_, enabled=enabled, config=config or {})


def _adapter(registry, instances, logger=None) -> TemplateConfigurationAdapter:
    manager = MagicMock()
    manager._registry = registry
    manager.config_manager.get_provider_config.return_value = SimpleNamespace(providers=instances)
    return TemplateConfigurationAdapter(template_manager=manager, logger=logger or MagicMock())


@pytest.fixture
def registry():
    from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import FieldMappingRegistry
    from orb.providers.azure.scheduler.hostfactory_field_mapping import AzureFieldMapping
    from orb.providers.gcp.scheduler.hostfactory_field_mapping import GCPFieldMapping

    # Bootstrap registers the HostFactory field mappings; mirror it here.
    saved = FieldMappingRegistry.all()
    FieldMappingRegistry.register("azure", AzureFieldMapping())
    FieldMappingRegistry.register("gcp", GCPFieldMapping())
    try:
        with freshly_registered_providers() as populated:
            yield populated
    finally:
        FieldMappingRegistry.clear()
        for key, value in saved.items():
            FieldMappingRegistry.register(key, value)


def _azure_row(api: str = "VMSS", **extra) -> dict:
    return {
        "template_id": "t1",
        "image_id": "img",
        "provider_api": api,
        "location": "eastus",
        "region": "westus",
        **extra,
    }


def _gcp_row(**extra) -> dict:
    return {
        "template_id": "t1",
        "image_id": "img",
        "provider_api": "MIG",
        "max_machines": 0,
        "machine_type": "n2-standard-2",
        "project_id": "orb-example-12345",
        "region": "us-central1",
        "zones": ["us-central1-a", "us-central1-b"],
        "source_image": "projects/p/global/images/i",
        **extra,
    }


def _has(errors: list[str], text: str) -> bool:
    return any(text in e for e in errors)


@pytest.mark.parametrize("api", ["VMSS", "SingleVM"])
def test_azure_row_resolved_by_provider_name(registry, api) -> None:
    adapter = _adapter(
        registry, [_instance("azure-1", "azure"), _instance("gcp-1", "gcp", GCP_CONFIG)]
    )
    errors = adapter.validate_template_config(_azure_row(api, provider_name="azure-1"))
    assert _has(errors, "conflicting")


def test_gcp_row_resolved_by_provider_name(registry) -> None:
    adapter = _adapter(
        registry, [_instance("azure-1", "azure"), _instance("gcp-1", "gcp", GCP_CONFIG)]
    )
    errors = adapter.validate_template_config(_gcp_row(provider_name="gcp-1"))
    assert _has(errors, "max_machines")


def test_single_vm_without_a_name_resolves_to_the_only_instance(registry) -> None:
    adapter = _adapter(registry, [_instance("azure-1", "azure")])
    errors = adapter.validate_template_config(_azure_row("SingleVM"))
    assert _has(errors, "conflicting")


def test_ambiguous_single_vm_applies_only_generic_rules(registry) -> None:
    logger = MagicMock()
    adapter = _adapter(
        registry, [_instance("azure-1", "azure"), _instance("gcp-1", "gcp", GCP_CONFIG)], logger
    )
    spy = MagicMock(wraps=registry.create_validator)
    registry.create_validator = spy
    try:
        errors = adapter.validate_template_config(_azure_row("SingleVM"))
    finally:
        del registry.create_validator
    spy.assert_not_called()
    assert not _has(errors, "conflicting")
    assert any("not resolved" in str(c.args[0]) for c in logger.warning.call_args_list)


def test_stamped_type_wins_over_provider_name(registry) -> None:
    adapter = _adapter(
        registry, [_instance("azure-1", "azure"), _instance("gcp-1", "gcp", GCP_CONFIG)]
    )
    errors = adapter.validate_template_config(
        _gcp_row(provider_name="azure-1", provider_type="gcp")
    )
    assert _has(errors, "max_machines")


def test_unknown_provider_name_falls_through_to_the_api_map(registry) -> None:
    adapter = _adapter(registry, [_instance("azure-1", "azure")])
    spy = MagicMock()
    registry.create_validator = spy
    try:
        adapter.validate_template_config(
            {
                "template_id": "t",
                "image_id": "i",
                "provider_api": "EC2Fleet",
                "provider_name": "nope",
            }
        )
    finally:
        del registry.create_validator
    spy.assert_not_called()


def test_provider_config_failure_is_unresolved_not_raised(registry) -> None:
    manager = MagicMock()
    manager._registry = registry
    manager.config_manager.get_provider_config.side_effect = RuntimeError("boom")
    adapter = TemplateConfigurationAdapter(template_manager=manager, logger=MagicMock())
    errors = adapter.validate_template_config(_azure_row())
    assert not _has(errors, "conflicting")


def test_named_instance_config_reaches_the_factory(registry) -> None:
    second = {**GCP_CONFIG, "project_id": "orb-second-12345"}
    adapter = _adapter(
        registry,
        [_instance("gcp-1", "gcp", GCP_CONFIG), _instance("gcp-2", "gcp", second)],
    )
    spy = MagicMock(wraps=registry.create_validator)
    registry.create_validator = spy
    try:
        adapter.validate_template_config(_gcp_row(provider_name="gcp-2", provider_type="gcp"))
        adapter.validate_template_config(_gcp_row(provider_type="gcp"))
    finally:
        del registry.create_validator
    assert spy.call_args_list[0].args == ("gcp", second)
    assert spy.call_args_list[1].args == ("gcp", GCP_CONFIG)


def test_unconfigured_instance_warns_and_applies_generic_rules(registry) -> None:
    logger = MagicMock()
    adapter = _adapter(registry, [_instance("azure-1", "azure")], logger)
    errors = adapter.validate_template_config(_gcp_row(provider_type="gcp"))
    assert not _has(errors, "max_machines")
    assert logger.warning.called


def test_failing_factory_warns_without_config_values(registry) -> None:
    secret = "sentinel-secret-value"
    logger = MagicMock()
    registry._logger = logger
    adapter = _adapter(
        registry, [_instance("gcp-1", "gcp", {**GCP_CONFIG, "token": secret})], logger
    )
    errors = adapter.validate_template_config(_gcp_row(provider_type="gcp"))
    logged = " ".join(str(a) for c in logger.warning.call_args_list for a in c.args)
    assert secret not in logged
    assert secret not in " ".join(errors)
    assert "gcp" in logged


# k8s and OCI keep the one-argument call and are not resolved from configuration.


def test_single_k8s_instance_is_not_used_to_resolve_a_type(registry) -> None:
    adapter = _adapter(registry, [_instance("k8s-1", "k8s")])
    spy = MagicMock()
    registry.create_validator = spy
    try:
        adapter.validate_template_config(
            {"template_id": "t", "image_id": "i", "provider_api": "Zzz"}
        )
    finally:
        del registry.create_validator
    spy.assert_not_called()


def test_missing_provider_api_with_one_k8s_instance_is_still_an_error(registry) -> None:
    adapter = _adapter(registry, [_instance("k8s-1", "k8s")])
    errors = adapter.validate_template_config({"template_id": "t", "image_id": "i"})
    assert "Provider API is required" in errors


def test_stamped_k8s_validator_is_called_with_one_argument(registry) -> None:
    adapter = _adapter(registry, [_instance("k8s-1", "k8s")])
    spy = MagicMock(wraps=registry.create_validator)
    registry.create_validator = spy
    try:
        adapter.validate_template_config(
            {"template_id": "t", "image_id": "i", "provider_api": "Pod", "provider_type": "k8s"}
        )
    finally:
        del registry.create_validator
    spy.assert_called_once_with("k8s")


# AWS rows are validated by the AWS adapter.


def _aws_row(**extra) -> dict:
    return {
        "template_id": "t",
        "provider_api": "RunInstances",
        "machine_image": "ami-0abc",
        "subnet_ids": ["subnet-1"],
        "security_group_ids": ["sg-1"],
        **extra,
    }


@pytest.mark.parametrize(
    "row, instances",
    [
        ({"provider_type": "aws"}, []),
        ({"provider_name": "aws-1"}, [_instance("aws-1", "aws"), _instance("a2", "azure")]),
        ({}, []),
    ],
    ids=["stamped", "by-name", "by-api-map"],
)
def test_aws_resolved_by_any_step_creates_a_validator(registry, row, instances) -> None:
    adapter = _adapter(registry, instances or [_instance("aws-1", "aws")])
    spy = MagicMock(wraps=registry.create_validator)
    registry.create_validator = spy
    try:
        errors = adapter.validate_template_config(_aws_row(**row))
    finally:
        del registry.create_validator
    assert spy.call_args.args[0] == "aws"
    assert errors == []


def test_aws_row_without_an_api_resolves_to_the_only_instance(registry) -> None:
    adapter = _adapter(registry, [_instance("aws-1", "aws")])
    errors = adapter.validate_template_config(_aws_row(provider_api=None, subnet_ids=["bad"]))
    assert errors == ["Provider API is required", "Invalid AWS subnet ID format: bad"]


def test_aws_row_with_bad_subnet_gets_the_provider_rule_error(registry) -> None:
    adapter = _adapter(registry, [_instance("aws-1", "aws")])
    errors = adapter.validate_template_config(_aws_row(subnet_ids=["bad"]))
    assert errors == ["Invalid AWS subnet ID format: bad"]


def test_microvm_resolves_to_aws(registry) -> None:
    adapter = _adapter(registry, [_instance("aws-1", "aws"), _instance("azure-1", "azure")])
    errors = adapter.validate_template_config(_aws_row(provider_api="MicroVM", subnet_ids=["bad"]))
    assert errors == ["Invalid AWS subnet ID format: bad"]


def test_single_aws_instance_is_not_assigned_to_a_row_with_another_providers_api(
    registry,
) -> None:
    adapter = _adapter(registry, [_instance("aws-1", "aws")])
    spy = MagicMock(wraps=registry.create_validator)
    registry.create_validator = spy
    try:
        errors = adapter.validate_template_config(_azure_row("VMSS"))
    finally:
        del registry.create_validator
    spy.assert_not_called()
    assert not _has(errors, "AWS")


def test_row_naming_its_aws_instance_is_still_validated_as_aws(registry) -> None:
    adapter = _adapter(registry, [_instance("aws-1", "aws"), _instance("azure-1", "azure")])
    errors = adapter.validate_template_config(_azure_row("VMSS", provider_name="aws-1"))
    assert "Unsupported AWS provider API: VMSS" in errors


def test_aws_adapter_receives_the_row_unrenamed(registry) -> None:
    adapter = _adapter(registry, [_instance("aws-1", "aws")])
    errors = adapter.validate_template_config(
        {
            "templateId": "t",
            "providerApi": "RunInstances",
            "imageId": "ami-0abc",
            "subnetIds": ["bad"],
        }
    )
    assert errors == ["Invalid AWS subnet ID format: bad"]


# HostFactory-format (camelCase) rows, as `templates validate --file` reads them.

_SSH_KEY = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"


def _hf_azure_row(**extra) -> dict:
    return {
        "templateId": "t1",
        "providerApi": "VMSS",
        "imageId": "Canonical:ubuntu:22_04-lts:latest",
        "resourceGroup": "rg",
        "location": "eastus2",
        "vmSize": "Standard_D4s_v5",
        "maxNumber": 3,
        "ssh_public_keys": [_SSH_KEY],
        **extra,
    }


def _hf_gcp_row(**extra) -> dict:
    return {
        "templateId": "t1",
        "providerApi": "MIG",
        "imageId": "projects/p/global/images/i",
        "maxNumber": 2,
        "vmType": "n2-standard-2",
        "projectId": "orb-example-12345",
        "region": "us-central1",
        "zones": ["us-central1-a", "us-central1-b"],
        **extra,
    }


def test_camel_case_azure_row_gets_no_extra_inputs_errors(registry) -> None:
    adapter = _adapter(registry, [_instance("azure-1", "azure")])
    errors = adapter.validate_template_config(_hf_azure_row())
    assert not _has(errors, "Extra inputs")
    assert not _has(errors, "Field required")


def test_camel_case_gcp_row_gets_no_extra_inputs_errors(registry) -> None:
    adapter = _adapter(registry, [_instance("gcp-1", "gcp", GCP_CONFIG)])
    errors = adapter.validate_template_config(_hf_gcp_row())
    assert not _has(errors, "Extra inputs")
    assert not _has(errors, "Field required")


def test_camel_case_row_still_reports_a_real_provider_error(registry) -> None:
    adapter = _adapter(registry, [_instance("azure-1", "azure")])
    errors = adapter.validate_template_config(_hf_azure_row(region="westus"))
    assert _has(errors, "conflicting")


def test_mixed_snake_and_camel_row_gets_no_extra_inputs_errors(registry) -> None:
    # The create path merges snake_case template_id/provider_api/image_id with
    # camelCase configuration keys.
    adapter = _adapter(registry, [_instance("azure-1", "azure")])
    row = {
        "template_id": "t1",
        "provider_api": "VMSS",
        "image_id": "Canonical:ubuntu:22_04-lts:latest",
        "resourceGroup": "rg",
        "location": "eastus2",
        "vmSize": "Standard_D4s_v5",
        "maxNumber": 3,
        "ssh_public_keys": [_SSH_KEY],
    }
    errors = adapter.validate_template_config(row)
    assert not _has(errors, "Extra inputs")
    assert not _has(errors, "Field required")


def test_normalisation_failure_skips_provider_rules_with_a_warning(registry, monkeypatch) -> None:
    logger = MagicMock()
    adapter = _adapter(registry, [_instance("azure-1", "azure")], logger)

    def boom(*_a, **_k):
        raise ValueError("secret-detail")

    monkeypatch.setattr(TemplateConfigurationAdapter, "_to_internal_fields", staticmethod(boom))
    errors = adapter.validate_template_config(_hf_azure_row(region="westus"))
    assert not _has(errors, "conflicting")
    logged = " ".join(str(a) for c in logger.warning.call_args_list for a in c.args)
    assert "ValueError" in logged
    assert "secret-detail" not in logged


def test_camel_case_azure_row_with_defaulted_max_number_gets_no_errors(registry) -> None:
    # Default resolution adds max_number beside the row's own maxNumber.
    adapter = _adapter(registry, [_instance("azure-1", "azure")])
    errors = adapter.validate_template_config(_hf_azure_row(maxNumber=5, max_number=10))
    assert not _has(errors, "Extra inputs")
    assert errors == []
