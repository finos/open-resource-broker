"""Tests for Azure native spec processing."""

from unittest.mock import Mock
from unittest.mock import patch

from pydantic import ValidationError

from application.services.native_spec_service import NativeSpecService
from config.schemas.provider_strategy_schema import ProviderConfig
from domain.request.aggregate import Request
from domain.request.request_types import RequestType
from providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from providers.azure.infrastructure.services.azure_native_spec_service import AzureNativeSpecService


def _make_template(**overrides):
    return AzureTemplate(
        template_id="azure-native-spec-test",
        provider_api="VMSS",
        vm_size="Standard_D4s_v5",
        resource_group="test-rg",
        location="eastus2",
        ssh_public_keys=["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
        image={
            "publisher": "Canonical",
            "offer": "0001-com-ubuntu-server-jammy",
            "sku": "22_04-lts-gen2",
            "version": "latest",
        },
        **overrides,
    )


def _make_request():
    return Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="azure-native-spec-test",
        machine_count=2,
        provider_type="azure",
        provider_instance="azure-default",
    )


def _make_provider_config(*, azure_extensions=None):
    return ProviderConfig(
        providers=[
            {
                "name": "azure-default",
                "type": "azure",
                "enabled": True,
                "config": {},
            }
        ],
        provider_defaults={
            "azure": {
                "extensions": azure_extensions,
            }
        } if azure_extensions is not None else {},
    )


def test_process_provider_api_spec_with_merge_merges_rendered_spec():
    config_port = Mock()
    config_port.get_native_spec_config.return_value = {"enabled": True, "merge_mode": "merge"}
    config_port.get_package_info.return_value = {"name": "orb", "version": "1.0.0"}
    config_port.get_provider_config.return_value = _make_provider_config()

    logger = Mock()
    spec_renderer = Mock()
    spec_renderer.render_spec.return_value = {
        "tags": {"Rendered": "{{ request_id }}"},
        "sku": {"name": "Standard_D8s_v5"},
    }

    native_spec_service = NativeSpecService(config_port, spec_renderer, logger)
    service = AzureNativeSpecService(native_spec_service, config_port)

    template = _make_template(provider_api_spec={"tags": {"Rendered": "{{ request_id }}"}})
    request = _make_request()
    default_payload = {"location": "eastus2", "sku": {"capacity": 2}}

    result = service.process_provider_api_spec_with_merge(template, request, default_payload)

    assert result["location"] == "eastus2"
    assert result["sku"]["capacity"] == 2
    assert result["sku"]["name"] == "Standard_D8s_v5"
    spec_renderer.render_spec.assert_called_once()


def test_process_provider_api_spec_with_merge_replace_mode_replaces_default():
    config_port = Mock()
    config_port.get_native_spec_config.return_value = {"enabled": True, "merge_mode": "replace"}
    config_port.get_package_info.return_value = {"name": "orb", "version": "1.0.0"}
    config_port.get_provider_config.return_value = _make_provider_config()

    logger = Mock()
    spec_renderer = Mock()
    spec_renderer.render_spec.return_value = {"location": "westus2"}

    native_spec_service = NativeSpecService(config_port, spec_renderer, logger)
    service = AzureNativeSpecService(native_spec_service, config_port)

    template = _make_template(provider_api_spec={"location": "{{ location }}"})
    request = _make_request()

    result = service.process_provider_api_spec_with_merge(
        template,
        request,
        {"location": "eastus2", "sku": {"capacity": 2}},
    )

    assert result == {"location": "westus2"}


def test_load_spec_file_uses_typed_provider_config_extensions_path():
    config_port = Mock()
    config_port.get_provider_config.return_value = _make_provider_config(
        azure_extensions={"native_spec": {"spec_file_base_path": "config/specs/azure"}}
    )

    service = AzureNativeSpecService(
        NativeSpecService(config_port, Mock(), Mock()),
        config_port,
    )

    with patch(
        "providers.azure.infrastructure.services.azure_native_spec_service.read_json_file"
    ) as mock_read:
        mock_read.return_value = {"location": "eastus2"}

        result = service._load_spec_file("vmss.json")

        assert result == {"location": "eastus2"}
        mock_read.assert_called_once_with("config/specs/azure/vmss.json")


def test_load_spec_file_rejects_non_object_native_spec_extensions():
    config_port = Mock()
    config_port.get_provider_config.return_value = _make_provider_config(
        azure_extensions={"native_spec": "config/specs/azure"}
    )

    service = AzureNativeSpecService(
        NativeSpecService(config_port, Mock(), Mock()),
        config_port,
    )

    try:
        service._load_spec_file("vmss.json")
    except ValidationError as exc:
        assert "native_spec" in str(exc)
    else:
        raise AssertionError("Expected ValidationError for malformed Azure native_spec config")
