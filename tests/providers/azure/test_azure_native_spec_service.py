"""Tests for Azure native spec processing."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from orb.application.services.native_spec_service import NativeSpecService
from orb.config.schemas.provider_strategy_schema import ProviderConfig
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType
from orb.providers.azure.exceptions.azure_exceptions import AzureValidationError
from orb.providers.azure.infrastructure.services.azure_native_spec_service import (
    AzureNativeSpecService,
)
from tests.providers.azure.strategy_test_support import make_azure_template


def _make_template(**overrides):
    return make_azure_template(
        template_id="azure-native-spec-test",
        provider_api="VMSS",
        **overrides,
    )


def _make_request():
    return Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="azure-native-spec-test",
        machine_count=2,
        provider_type="azure",
        provider_name="azure-default",
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
        }
        if azure_extensions is not None
        else {},
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
        "orb.providers.azure.infrastructure.services.azure_native_spec_service.read_json_file"
    ) as mock_read:
        mock_read.return_value = {"location": "eastus2"}

        result = service._load_spec_file("vmss.json")

        assert result == {"location": "eastus2"}
        expected_path = Path("config/specs/azure/vmss.json").resolve()
        mock_read.assert_called_once_with(str(expected_path))


def test_load_spec_file_accepts_nested_file_inside_configured_base(tmp_path):
    spec_base = tmp_path / "specs"
    nested_spec = spec_base / "nested" / "vmss.json"
    nested_spec.parent.mkdir(parents=True)
    nested_spec.write_text('{"location": "eastus2"}', encoding="utf-8")

    config_port = Mock()
    config_port.get_provider_config.return_value = _make_provider_config(
        azure_extensions={"native_spec": {"spec_file_base_path": str(spec_base)}}
    )
    service = AzureNativeSpecService(
        NativeSpecService(config_port, Mock(), Mock()),
        config_port,
    )

    assert service._load_spec_file("nested/vmss.json") == {"location": "eastus2"}


def test_load_spec_file_rejects_absolute_path(tmp_path):
    outside_spec = tmp_path / "outside.json"
    outside_spec.write_text("{}", encoding="utf-8")

    config_port = Mock()
    config_port.get_provider_config.return_value = _make_provider_config(
        azure_extensions={"native_spec": {"spec_file_base_path": str(tmp_path / "specs")}}
    )
    service = AzureNativeSpecService(
        NativeSpecService(config_port, Mock(), Mock()),
        config_port,
    )

    with pytest.raises(AzureValidationError, match="must be relative"):
        service._load_spec_file(str(outside_spec))


def test_load_spec_file_rejects_parent_traversal(tmp_path):
    spec_base = tmp_path / "specs"
    spec_base.mkdir()
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")

    config_port = Mock()
    config_port.get_provider_config.return_value = _make_provider_config(
        azure_extensions={"native_spec": {"spec_file_base_path": str(spec_base)}}
    )
    service = AzureNativeSpecService(
        NativeSpecService(config_port, Mock(), Mock()),
        config_port,
    )

    with pytest.raises(AzureValidationError, match=r"must not contain '\.\.'"):
        service._load_spec_file("../outside.json")


def test_load_spec_file_rejects_symlink_escape(tmp_path):
    spec_base = tmp_path / "specs"
    spec_base.mkdir()
    outside_spec = tmp_path / "outside.json"
    outside_spec.write_text("{}", encoding="utf-8")
    (spec_base / "linked.json").symlink_to(outside_spec)

    config_port = Mock()
    config_port.get_provider_config.return_value = _make_provider_config(
        azure_extensions={"native_spec": {"spec_file_base_path": str(spec_base)}}
    )
    service = AzureNativeSpecService(
        NativeSpecService(config_port, Mock(), Mock()),
        config_port,
    )

    with pytest.raises(AzureValidationError, match="resolve inside"):
        service._load_spec_file("linked.json")


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
