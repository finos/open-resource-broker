"""Tests for HostFactorySchedulerStrategy _map_template_fields and format_request_response."""

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)


def make_strategy():
    """Return a HostFactorySchedulerStrategy with DI bypassed."""
    strategy = HostFactorySchedulerStrategy()
    strategy._logger = MagicMock()
    return strategy


class TestMapTemplateFieldsCallsApplyTemplateDefaults:
    """_map_template_fields must delegate to _apply_template_defaults, not call resolve inline."""

    def setup_method(self):
        self.strategy = make_strategy()
        # Provide a minimal field mapper that returns the input unchanged
        mock_mapper = MagicMock()
        mock_mapper.map_input_fields.side_effect = dict
        self.strategy._field_mapper = mock_mapper

    def _minimal_template(self):
        return {
            "templateId": "t-hf-1",
            "vmType": "t2.micro",
            "imageId": "ami-abc",
            "subnetIds": ["subnet-1"],
        }

    def test_apply_template_defaults_called_when_service_present(self):
        mock_service = MagicMock()
        mock_service.resolve_template_defaults.side_effect = lambda t, p: t
        mock_service.resolve_provider_api_default.return_value = "EC2Fleet"
        self.strategy._template_defaults_service = cast(None, mock_service)

        with patch.object(self.strategy, "_get_provider_name", return_value="test-provider"):
            self.strategy._map_template_fields(self._minimal_template(), None)

        mock_service.resolve_template_defaults.assert_called_once()

    def test_apply_template_defaults_not_called_when_service_none(self):
        self.strategy._template_defaults_service = cast(None, None)

        with (
            patch("orb.infrastructure.di.container.is_container_ready", return_value=False),
            patch.object(self.strategy, "_get_provider_name", return_value="test-provider"),
        ):
            result = self.strategy._map_template_fields(self._minimal_template(), None)

        # Should complete without error and return a dict
        assert isinstance(result, dict)

    def test_provider_override_passed_to_apply_template_defaults(self):
        mock_service = MagicMock()
        mock_service.resolve_template_defaults.side_effect = lambda t, p: t
        mock_service.resolve_provider_api_default.return_value = "EC2Fleet"
        self.strategy._template_defaults_service = cast(None, mock_service)

        self.strategy._map_template_fields(self._minimal_template(), "override-provider")

        call_args = mock_service.resolve_template_defaults.call_args
        assert call_args[0][1] == "override-provider"

    def test_raises_on_none_template(self):
        with pytest.raises(ValueError, match="None"):
            self.strategy._map_template_fields(cast(dict[str, Any], None), None)

    def test_raises_on_non_dict_template(self):
        with pytest.raises(ValueError):
            self.strategy._map_template_fields(cast(dict[str, Any], "not-a-dict"), None)

    def test_declared_provider_selects_its_field_mapping(self):
        from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import (
            FieldMappingRegistry,
        )
        from orb.providers.gcp.scheduler.hostfactory_field_mapping import GCPFieldMapping

        self.strategy._template_defaults_service = cast(None, MagicMock())
        self.strategy._template_defaults_service.resolve_provider_api_default.return_value = "MIG"
        self.strategy._template_defaults_service.resolve_template_defaults.side_effect = (
            lambda template, provider: template
        )

        with (
            patch.object(self.strategy, "_get_provider_name", return_value="azure-primary"),
            patch.object(self.strategy, "_get_active_provider_type", return_value="azure"),
            patch.object(
                FieldMappingRegistry,
                "get_or_none",
                side_effect=lambda provider_type: (
                    GCPFieldMapping() if provider_type == "gcp" else None
                ),
            ),
        ):
            mapped = self.strategy._map_template_fields(
                {"templateId": "gcp-template", "providerType": "gcp", "vmType": "e2-micro"}
            )

        assert mapped["provider_type"] == "gcp"
        assert mapped["machine_type"] == "e2-micro"


class TestHFFormatRequestResponseUsesCoerceAndUnwrap:
    """format_request_response must use _coerce_to_dict and _unwrap_request_id."""

    def setup_method(self):
        self.strategy = make_strategy()

    def test_dict_input_pending(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-hf-1", "status": "pending"}
        )
        assert result.get("requestId") == "req-hf-1"
        assert "message" in result

    def test_pydantic_model_input_coerced(self):
        obj = MagicMock()
        obj.model_dump.return_value = {"request_id": "req-hf-2", "status": "complete"}
        result = self.strategy.format_request_response(obj)
        assert result.get("requestId") == "req-hf-2"

    def test_to_dict_object_coerced(self):
        obj = MagicMock(spec=["to_dict"])
        obj.to_dict.return_value = {"request_id": "req-hf-3", "status": "pending"}
        result = self.strategy.format_request_response(obj)
        assert result.get("requestId") == "req-hf-3"

    def test_request_id_as_dict_with_value_unwrapped(self):
        data = {"request_id": {"value": "req-hf-wrapped"}, "status": "complete"}
        result = self.strategy.format_request_response(data)
        assert result.get("requestId") == "req-hf-wrapped"

    def test_request_id_as_object_with_value_attr_unwrapped(self):
        id_obj = MagicMock(spec=["value"])
        id_obj.value = "req-hf-obj"
        data = {"request_id": id_obj, "status": "pending"}
        result = self.strategy.format_request_response(data)
        assert result.get("requestId") == "req-hf-obj"

    def test_failed_status(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-hf-4", "status": "failed"}
        )
        assert "message" in result
        assert "failed" in result["message"].lower()

    def test_cancelled_status(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-hf-5", "status": "cancelled"}
        )
        assert "message" in result
        assert "cancelled" in result["message"].lower()

    def test_complete_status(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-hf-6", "status": "complete"}
        )
        assert result.get("requestId") == "req-hf-6"
        assert "completed" in result["message"].lower()

    def test_requests_list_passthrough(self):
        data = {
            "requests": [{"requestId": "r1"}],
            "status": "complete",
            "message": "done",
        }
        result = self.strategy.format_request_response(data)
        assert "requests" in result
        assert result["requests"] == [{"requestId": "r1"}]


class TestMapTemplateFieldsTransformsOnce:
    """The field mapper already applies the HostFactory transformations."""

    def setup_method(self):
        from orb.infrastructure.scheduler.hostfactory.field_mapper import HostFactoryFieldMapper

        self.strategy = make_strategy()
        self.strategy._field_mapper = HostFactoryFieldMapper("aws")
        self.strategy._template_defaults_service = cast(None, None)

    def _map(self, template: dict[str, Any]) -> dict[str, Any]:
        with (
            patch("orb.infrastructure.di.container.is_container_ready", return_value=False),
            patch.object(self.strategy, "_get_provider_name", return_value="test-provider"),
        ):
            return self.strategy._map_template_fields(template, None)

    def test_transformations_applied_once(self):
        from orb.infrastructure.scheduler.hostfactory.transformations import (
            HostFactoryTransformations,
        )

        with patch.object(
            HostFactoryTransformations,
            "apply_transformations",
            side_effect=lambda mapped: mapped,
        ) as spy:
            self._map({"templateId": "t1", "vmType": "t2.micro", "imageId": "ami-abc"})

        assert spy.call_count == 1

    def test_user_data_file_is_read_once(self, tmp_path):
        # The script's only line names another existing file: a second pass
        # would read that file in place of the script.
        other = tmp_path / "other.txt"
        other.write_text("other file content\n")
        script = tmp_path / "script.sh"
        script.write_text(str(other))

        result = self._map(
            {
                "templateId": "t1",
                "vmType": "t2.micro",
                "imageId": "ami-abc",
                "userDataScript": str(script),
            }
        )

        assert result["machine_bootstrap"] == str(other)

    def test_volume_fields_map_to_internal_names(self):
        result = self._map(
            {
                "templateId": "t1",
                "vmType": "t2.micro",
                "imageId": "ami-abc",
                "rootDeviceVolumeSize": 50,
                "volumeType": "gp3",
            }
        )

        assert result["machine_disk_size_gb"] == 50
        assert result["machine_disk_type"] == "gp3"
