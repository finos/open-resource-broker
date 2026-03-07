"""Extended tests for DefaultSchedulerStrategy - format_request_response and load_templates_from_path."""

import json
import os
import tempfile
from typing import cast
from unittest.mock import MagicMock, patch

from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)


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
        result = self.strategy.format_request_response(
            {"request_id": "req-2", "status": "complete"}
        )
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
        result = self.strategy.format_request_response(
            {"request_id": "req-5", "status": "cancelled"}
        )
        assert "error" in result
        assert result["request_id"] == "req-5"

    def test_pending_status(self):
        result = self.strategy.format_request_response({"request_id": "req-6", "status": "pending"})
        assert result["request_id"] == "req-6"
        assert "message" in result

    def test_complete_status(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-7", "status": "complete"}
        )
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
    """Tests for DefaultSchedulerStrategy.format_request_status_response (IBM HF spec mapping)."""

    def setup_method(self):
        self.strategy = make_strategy()

    def test_returns_hf_mapped_statuses(self):
        dto1 = MagicMock()
        dto1.to_dict.return_value = {"request_id": "r1", "status": "complete"}
        dto2 = MagicMock()
        dto2.to_dict.return_value = {"request_id": "r2", "status": "pending"}

        result = self.strategy.format_request_status_response([dto1, dto2])

        assert "requests" in result
        assert len(result["requests"]) == 2
        assert result["requests"][0]["request_id"] == "r1"
        # "complete" -> "complete", "pending" -> "running"
        assert result["requests"][0]["status"] == "complete"
        assert result["requests"][1]["status"] == "running"

    def test_domain_statuses_mapped_to_hf_spec(self):
        # IBM HF spec only allows: running, complete, complete_with_error
        cases = [
            ("pending", "running"),
            ("in_progress", "running"),
            ("complete", "complete"),
            ("failed", "complete_with_error"),
            ("partial", "complete_with_error"),
            ("cancelled", "complete_with_error"),
            ("timeout", "complete_with_error"),
        ]
        for domain_status, expected_hf_status in cases:
            dto = MagicMock()
            dto.to_dict.return_value = {"request_id": "r1", "status": domain_status}
            result = self.strategy.format_request_status_response([dto])
            assert result["requests"][0]["status"] == expected_hf_status, (
                f"Expected {domain_status!r} -> {expected_hf_status!r}"
            )

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


def _write_json(data: dict) -> str:
    """Write JSON to a temp file and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


class TestCrossSchedulerDelegation:
    """Tests for cross-scheduler template loading via registry delegation."""

    def setup_method(self):
        self.default_strategy = DefaultSchedulerStrategy()
        self.default_strategy._logger = MagicMock()
        self.hf_strategy = HostFactorySchedulerStrategy()
        self.hf_strategy._logger = MagicMock()

    def test_default_delegates_to_hf_when_scheduler_type_is_hostfactory(self):
        hf_templates = {
            "scheduler_type": "hostfactory",
            "templates": [{"templateId": "t1", "vmType": "t3.medium", "maxNumber": 5}],
        }
        path = _write_json(hf_templates)
        try:
            mock_delegate_result = [{"template_id": "t1", "instance_type": "t3.medium"}]
            with patch.object(
                self.default_strategy,
                "_delegate_load_to_strategy",
                return_value=mock_delegate_result,
            ) as mock_delegate:
                result = self.default_strategy.load_templates_from_path(path)

            mock_delegate.assert_called_once_with("hostfactory", path, None)
            assert result == mock_delegate_result
        finally:
            os.unlink(path)

    def test_default_falls_back_to_best_effort_when_delegation_returns_none(self):
        templates_data = {
            "scheduler_type": "hostfactory",
            "templates": [{"template_id": "t1"}],
        }
        path = _write_json(templates_data)
        try:
            with patch.object(
                self.default_strategy, "_delegate_load_to_strategy", return_value=None
            ):
                result = self.default_strategy.load_templates_from_path(path)

            # Falls back to best-effort: loads raw templates without field mapping
            assert len(result) == 1
            assert result[0]["template_id"] == "t1"
        finally:
            os.unlink(path)

    def test_default_loads_normally_when_scheduler_type_matches(self):
        templates_data = {
            "scheduler_type": "default",
            "templates": [{"template_id": "t1", "image_id": "ami-abc"}],
        }
        path = _write_json(templates_data)
        try:
            with patch.object(self.default_strategy, "_delegate_load_to_strategy") as mock_delegate:
                result = self.default_strategy.load_templates_from_path(path)

            mock_delegate.assert_not_called()
            assert len(result) == 1
            assert result[0]["template_id"] == "t1"
        finally:
            os.unlink(path)

    def test_default_loads_normally_when_no_scheduler_type_in_file(self):
        templates_data = {"templates": [{"template_id": "t1"}]}
        path = _write_json(templates_data)
        try:
            with patch.object(self.default_strategy, "_delegate_load_to_strategy") as mock_delegate:
                result = self.default_strategy.load_templates_from_path(path)

            mock_delegate.assert_not_called()
            assert len(result) == 1
        finally:
            os.unlink(path)

    def test_hf_delegates_to_default_when_scheduler_type_is_default(self):
        default_templates = {
            "scheduler_type": "default",
            "templates": [{"template_id": "t1", "image_id": "ami-xyz"}],
        }
        path = _write_json(default_templates)
        try:
            mock_delegate_result = [{"template_id": "t1", "image_id": "ami-xyz"}]
            with patch.object(
                self.hf_strategy,
                "_delegate_load_to_strategy",
                return_value=mock_delegate_result,
            ) as mock_delegate:
                result = self.hf_strategy.load_templates_from_path(path)

            mock_delegate.assert_called_once_with("default", path, None)
            assert result == mock_delegate_result
        finally:
            os.unlink(path)

    def test_hf_falls_back_to_best_effort_when_delegation_returns_none(self):
        templates_data = {
            "scheduler_type": "default",
            "templates": [{"templateId": "t1", "vmType": "t3.small"}],
        }
        path = _write_json(templates_data)
        try:
            with patch.object(self.hf_strategy, "_delegate_load_to_strategy", return_value=None):
                # Should not raise — falls back to HF field mapping
                result = self.hf_strategy.load_templates_from_path(path)

            assert isinstance(result, list)
        finally:
            os.unlink(path)

    def test_hf_loads_normally_when_scheduler_type_matches(self):
        templates_data = {
            "scheduler_type": "hostfactory",
            "templates": [{"templateId": "t1", "vmType": "t3.large", "maxNumber": 2}],
        }
        path = _write_json(templates_data)
        try:
            with patch.object(self.hf_strategy, "_delegate_load_to_strategy") as mock_delegate:
                result = self.hf_strategy.load_templates_from_path(path)

            mock_delegate.assert_not_called()
            assert len(result) == 1
        finally:
            os.unlink(path)

    def test_hf_loads_normally_when_no_scheduler_type_in_file(self):
        # Legacy files without scheduler_type should load via HF path (backward compat)
        templates_data = {"templates": [{"templateId": "t1", "vmType": "t3.micro"}]}
        path = _write_json(templates_data)
        try:
            with patch.object(self.hf_strategy, "_delegate_load_to_strategy") as mock_delegate:
                result = self.hf_strategy.load_templates_from_path(path)

            mock_delegate.assert_not_called()
            assert len(result) == 1
        finally:
            os.unlink(path)

    def test_delegate_load_warns_and_returns_none_for_unregistered_type(self):
        strategy = DefaultSchedulerStrategy()
        strategy._logger = MagicMock()

        with patch(
            "orb.infrastructure.scheduler.registry.get_scheduler_registry"
        ) as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.is_registered.return_value = False
            mock_get_registry.return_value = mock_registry

            result = strategy._delegate_load_to_strategy("unknown_type", "/some/path.json")

        assert result is None
        strategy._logger.warning.assert_called_once()

    def test_delegate_load_returns_none_on_construction_failure(self):
        strategy = DefaultSchedulerStrategy()
        strategy._logger = MagicMock()

        with patch(
            "orb.infrastructure.scheduler.registry.get_scheduler_registry"
        ) as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.is_registered.return_value = True
            mock_registry.get_strategy_class.side_effect = RuntimeError("boom")
            mock_get_registry.return_value = mock_registry

            result = strategy._delegate_load_to_strategy("hostfactory", "/some/path.json")

        assert result is None
        strategy._logger.warning.assert_called_once()

    def test_delegate_passes_provider_override(self):
        strategy = DefaultSchedulerStrategy()
        strategy._logger = MagicMock()

        mock_delegate_strategy = MagicMock()
        mock_delegate_strategy.load_templates_from_path.return_value = [{"template_id": "t1"}]

        with patch(
            "orb.infrastructure.scheduler.registry.get_scheduler_registry"
        ) as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.is_registered.return_value = True
            mock_registry.get_strategy_class.return_value = MagicMock(
                return_value=mock_delegate_strategy
            )
            mock_get_registry.return_value = mock_registry

            result = strategy._delegate_load_to_strategy(
                "hostfactory", "/some/path.json", "my-provider"
            )

        mock_delegate_strategy.load_templates_from_path.assert_called_once_with(
            "/some/path.json", "my-provider"
        )
        assert result == [{"template_id": "t1"}]
