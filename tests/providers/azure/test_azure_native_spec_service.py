"""Tests for Azure native spec processing."""

from unittest.mock import Mock

from application.services.native_spec_service import NativeSpecService
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


def test_process_provider_api_spec_with_merge_merges_rendered_spec():
    config_port = Mock()
    config_port.get_native_spec_config.return_value = {"enabled": True, "merge_mode": "merge"}
    config_port.get_package_info.return_value = {"name": "orb", "version": "1.0.0"}
    config_port.get_provider_config.return_value = {}

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
    config_port.get_provider_config.return_value = {}

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
