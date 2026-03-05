"""Tests for HostFactorySchedulerStrategy _map_template_fields and format_request_response."""

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from infrastructure.scheduler.hostfactory.hostfactory_strategy import HostFactorySchedulerStrategy


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
        mock_mapper.map_input_fields.side_effect = lambda t: dict(t)
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
            patch("infrastructure.di.container.is_container_ready", return_value=False),
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
