"""Extended tests for DefaultSchedulerStrategy - format_request_response and load_templates_from_path."""

import json
import os
import tempfile
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy


def make_strategy():
    return DefaultSchedulerStrategy()


class TestFormatRequestResponse:
    """Tests for DefaultSchedulerStrategy.format_request_response."""

    def setup_method(self):
        self.strategy = make_strategy()

    def test_dict_input_pending_status(self):
        result = self.strategy.format_request_response({"request_id": "req-1", "status": "pending"})
        assert result["request_id"] == "req-1"
        assert "message" in result

    def test_dict_input_complete_status(self):
        result = self.strategy.format_request_response({"request_id": "req-2", "status": "complete"})
        assert result["request_id"] == "req-2"
        assert result["message"] == "Request completed successfully"

    def test_pydantic_model_input(self):
        obj = MagicMock()
        obj.model_dump.return_value = {"request_id": "req-pydantic", "status": "pending"}
        result = self.strategy.format_request_response(obj)
        assert result["request_id"] == "req-pydantic"

    def test_status_message_field_used_in_failed(self):
        data = {"request_id": "req-3", "status": "failed", "status_message": "out of capacity"}
        result = self.strategy.format_request_response(data)
        assert "error" in result
        assert "out of capacity" in result["error"]

    def test_failed_status_returns_error_key(self):
        result = self.strategy.format_request_response({"request_id": "req-4", "status": "failed"})
        assert "error" in result
        assert result["request_id"] == "req-4"

    def test_cancelled_status(self):
        result = self.strategy.format_request_response({"request_id": "req-5", "status": "cancelled"})
        assert "error" in result
        assert result["request_id"] == "req-5"

    def test_pending_status(self):
        result = self.strategy.format_request_response({"request_id": "req-6", "status": "pending"})
        assert result["request_id"] == "req-6"
        assert "message" in result

    def test_complete_status(self):
        result = self.strategy.format_request_response({"request_id": "req-7", "status": "complete"})
        assert result["request_id"] == "req-7"
        assert "message" in result

    def test_requests_list_passthrough(self):
        data = {
            "requests": [{"request_id": "r1"}],
            "status": "complete",
            "message": "done",
        }
        result = self.strategy.format_request_response(data)
        assert "requests" in result
        assert result["requests"] == [{"request_id": "r1"}]


class TestFormatRequestStatusResponse:
    """Tests for DefaultSchedulerStrategy.format_request_status_response (domain pass-through)."""

    def setup_method(self):
        self.strategy = make_strategy()

    def test_returns_domain_native_format(self):
        dto1 = MagicMock()
        dto1.to_dict.return_value = {"request_id": "r1", "status": "complete"}
        dto2 = MagicMock()
        dto2.to_dict.return_value = {"request_id": "r2", "status": "pending"}

        result = self.strategy.format_request_status_response([dto1, dto2])

        assert "requests" in result
        assert len(result["requests"]) == 2
        assert result["requests"][0]["request_id"] == "r1"
        assert result["requests"][1]["status"] == "pending"

    def test_status_strings_passed_through_unchanged(self):
        dto = MagicMock()
        dto.to_dict.return_value = {"request_id": "r1", "status": "in_progress"}
        result = self.strategy.format_request_status_response([dto])
        # Domain status values must not be remapped
        assert result["requests"][0]["status"] == "in_progress"

    def test_count_field_present(self):
        dto = MagicMock()
        dto.to_dict.return_value = {"request_id": "r1", "status": "complete"}
        result = self.strategy.format_request_status_response([dto])
        assert result["count"] == 1

    def test_empty_list(self):
        result = self.strategy.format_request_status_response([])
        assert result["requests"] == []
        assert result["count"] == 0


class TestLoadTemplatesFromPath:
    """Tests for DefaultSchedulerStrategy.load_templates_from_path calling _apply_template_defaults."""

    def setup_method(self):
        self.strategy = make_strategy()
        # Provide a logger so debug/error calls don't fail
        self.strategy._logger = MagicMock()

    def test_apply_template_defaults_called_for_each_template(self):
        templates_data = {
            "scheduler_type": "default",
            "templates": [
                {"template_id": "t1", "image_id": "ami-1"},
                {"template_id": "t2", "image_id": "ami-2"},
            ],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(templates_data, f)
            path = f.name

        try:
            mock_service = MagicMock()
            mock_service.resolve_template_defaults.side_effect = lambda t, p: t
            self.strategy._template_defaults_service = cast(None, mock_service)

            with patch.object(self.strategy, "_get_provider_name", return_value="test-provider"):
                result = self.strategy.load_templates_from_path(path)

            assert mock_service.resolve_template_defaults.call_count == 2
            assert len(result) == 2
        finally:
            os.unlink(path)

    def test_returns_empty_list_for_missing_file(self):
        result = self.strategy.load_templates_from_path("/nonexistent/path/templates.json")
        assert result == []

    def test_provider_override_passed_to_apply_defaults(self):
        templates_data = {
            "scheduler_type": "default",
            "templates": [{"template_id": "t1"}],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(templates_data, f)
            path = f.name

        try:
            mock_service = MagicMock()
            mock_service.resolve_template_defaults.side_effect = lambda t, p: t
            self.strategy._template_defaults_service = cast(None, mock_service)

            self.strategy.load_templates_from_path(path, provider_override="override-provider")

            call_args = mock_service.resolve_template_defaults.call_args
            assert call_args[0][1] == "override-provider"
        finally:
            os.unlink(path)
