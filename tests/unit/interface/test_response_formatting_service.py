"""Unit tests for ResponseFormattingService."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.ports.scheduler_port import SchedulerPort
from orb.interface.response_formatting_service import ResponseFormattingService


def _make_service() -> tuple[ResponseFormattingService, MagicMock]:
    scheduler = MagicMock(spec=SchedulerPort)
    scheduler.format_request_response.return_value = {"formatted": True}
    scheduler.get_exit_code_for_status.return_value = 0
    scheduler.format_request_status_response.return_value = {"requests": []}
    scheduler.format_return_requests_response.return_value = {"return_requests": []}
    scheduler.format_machine_status_response.return_value = {"machines": []}
    scheduler.format_machine_details_response.return_value = {"machine_id": "m-1"}
    scheduler.format_templates_response.return_value = {"templates": []}
    scheduler.format_template_mutation_response.return_value = {"template_id": "t-1"}
    scheduler.format_system_status_response.return_value = {"status": "ok"}
    scheduler.format_provider_detail_response.return_value = {"provider": "aws"}
    scheduler.format_storage_test_response.return_value = {"success": True}
    svc = ResponseFormattingService(scheduler)
    return svc, scheduler


@pytest.mark.unit
class TestFormatRequestOperation:
    def test_delegates_to_scheduler_and_returns_interface_response(self):
        svc, scheduler = _make_service()
        scheduler.format_request_response.return_value = {"request_id": "req-1"}
        scheduler.get_exit_code_for_status.return_value = 0

        result = svc.format_request_operation({"request_id": "req-1"}, "pending")

        assert isinstance(result, InterfaceResponse)
        assert result.exit_code == 0
        assert result.data == {"request_id": "req-1"}
        scheduler.format_request_response.assert_called_once_with({"request_id": "req-1"})
        scheduler.get_exit_code_for_status.assert_called_once_with("pending")

    def test_non_zero_exit_code_forwarded(self):
        svc, scheduler = _make_service()
        scheduler.get_exit_code_for_status.return_value = 2
        result = svc.format_request_operation({}, "error")
        assert result.exit_code == 2


@pytest.mark.unit
class TestFormatRequestStatus:
    def test_basic_call_returns_interface_response(self):
        svc, scheduler = _make_service()
        scheduler.format_request_status_response.return_value = {"requests": [{"id": "r1"}]}
        result = svc.format_request_status([{"id": "r1"}])
        assert isinstance(result, InterfaceResponse)
        scheduler.format_request_status_response.assert_called_once_with([{"id": "r1"}])

    def test_total_count_appended_when_provided(self):
        svc, scheduler = _make_service()
        scheduler.format_request_status_response.return_value = {"requests": []}
        result = svc.format_request_status([], total_count=42)
        assert result.data["total_count"] == 42

    def test_next_cursor_appended_when_provided(self):
        svc, scheduler = _make_service()
        scheduler.format_request_status_response.return_value = {"requests": []}
        result = svc.format_request_status([], next_cursor="cursor123")
        assert result.data["next_cursor"] == "cursor123"

    def test_no_pagination_kwargs_leaves_payload_clean(self):
        """Single-request / HostFactory getRequestStatus path: caller passes no
        pagination kwargs, so next_cursor/total_count must NOT be injected.

        The IBM Symphony HF wire spec has no pagination cursor on a single
        request-status response — stamping ``next_cursor: null`` there leaks a
        field into external HF integrations.
        """
        svc, scheduler = _make_service()
        scheduler.format_request_status_response.return_value = {"requests": []}
        result = svc.format_request_status([])
        assert "next_cursor" not in result.data
        assert "total_count" not in result.data

    def test_explicit_none_cursor_is_stamped_for_list_last_page(self):
        """LIST caller that explicitly passes next_cursor=None (a last page)
        still gets the key stamped so UI load-more can read it."""
        svc, scheduler = _make_service()
        scheduler.format_request_status_response.return_value = {"requests": []}
        result = svc.format_request_status([], total_count=3, next_cursor=None)
        assert result.data["next_cursor"] is None
        assert result.data["total_count"] == 3

    def test_next_cursor_not_overwritten_when_data_already_has_it(self):
        svc, scheduler = _make_service()
        scheduler.format_request_status_response.return_value = {
            "requests": [],
            "next_cursor": "existing",
        }
        # Don't pass next_cursor kwarg — should preserve existing value untouched
        result = svc.format_request_status([])
        assert result.data["next_cursor"] == "existing"

    def test_data_is_non_dict_not_mutated(self):
        svc, scheduler = _make_service()
        # Return a list, not a dict — method should not crash
        scheduler.format_request_status_response.return_value = [{"id": "r1"}]
        result = svc.format_request_status([{"id": "r1"}], total_count=1)
        # Result is the raw list, no key was injected
        assert isinstance(result.data, list)


@pytest.mark.unit
class TestFormatReturnRequests:
    def test_delegates_to_scheduler(self):
        svc, scheduler = _make_service()
        scheduler.format_return_requests_response.return_value = {
            "return_requests": [{"id": "rr1"}]
        }
        result = svc.format_return_requests([{"id": "rr1"}])
        assert isinstance(result, InterfaceResponse)
        assert result.data == {"return_requests": [{"id": "rr1"}]}
        scheduler.format_return_requests_response.assert_called_once_with([{"id": "rr1"}])


@pytest.mark.unit
class TestFormatMachineList:
    def test_basic_call(self):
        svc, scheduler = _make_service()
        scheduler.format_machine_status_response.return_value = {"machines": []}
        result = svc.format_machine_list([])
        assert isinstance(result, InterfaceResponse)

    def test_total_count_appended(self):
        svc, scheduler = _make_service()
        scheduler.format_machine_status_response.return_value = {"machines": []}
        result = svc.format_machine_list([], total_count=7)
        assert result.data["total_count"] == 7

    def test_next_cursor_appended(self):
        svc, scheduler = _make_service()
        scheduler.format_machine_status_response.return_value = {"machines": []}
        result = svc.format_machine_list([], next_cursor="pg2")
        assert result.data["next_cursor"] == "pg2"

    def test_no_pagination_kwargs_leaves_payload_clean(self):
        """Single-machine listing (no kwargs) must not gain pagination fields."""
        svc, scheduler = _make_service()
        scheduler.format_machine_status_response.return_value = {"machines": []}
        result = svc.format_machine_list([])
        assert "next_cursor" not in result.data
        assert "total_count" not in result.data


@pytest.mark.unit
class TestFormatMachineDetail:
    def test_delegates_to_scheduler(self):
        svc, scheduler = _make_service()
        scheduler.format_machine_details_response.return_value = {"machine_id": "m-abc"}
        result = svc.format_machine_detail({"machine_id": "m-abc"})
        assert isinstance(result, InterfaceResponse)
        assert result.data == {"machine_id": "m-abc"}


@pytest.mark.unit
class TestFormatTemplateList:
    def test_basic_call(self):
        svc, scheduler = _make_service()
        scheduler.format_templates_response.return_value = {"templates": []}
        result = svc.format_template_list([])
        assert isinstance(result, InterfaceResponse)

    def test_total_count_and_cursor(self):
        svc, scheduler = _make_service()
        scheduler.format_templates_response.return_value = {"templates": []}
        result = svc.format_template_list([], total_count=5, next_cursor="c1")
        assert result.data["total_count"] == 5
        assert result.data["next_cursor"] == "c1"

    def test_no_pagination_kwargs_leaves_payload_clean(self):
        """Refresh/list without kwargs must not gain pagination fields."""
        svc, scheduler = _make_service()
        scheduler.format_templates_response.return_value = {"templates": []}
        result = svc.format_template_list([])
        assert "next_cursor" not in result.data
        assert "total_count" not in result.data


@pytest.mark.unit
class TestFormatTemplateMutation:
    def test_delegates_to_scheduler(self):
        svc, scheduler = _make_service()
        scheduler.format_template_mutation_response.return_value = {"template_id": "t-99"}
        result = svc.format_template_mutation({"template_id": "t-99"})
        assert isinstance(result, InterfaceResponse)
        assert result.data == {"template_id": "t-99"}


@pytest.mark.unit
class TestFormatSchedulerStrategyList:
    def test_constructs_expected_shape(self):
        svc, _ = _make_service()
        result = svc.format_scheduler_strategy_list(["s1", "s2"], "s1", 2)
        assert isinstance(result, InterfaceResponse)
        assert result.data == {"strategies": ["s1", "s2"], "current_strategy": "s1", "count": 2}


@pytest.mark.unit
class TestFormatSchedulerConfig:
    def test_wraps_config_in_config_key(self):
        svc, _ = _make_service()
        cfg = {"param": "value"}
        result = svc.format_scheduler_config(cfg)
        assert isinstance(result, InterfaceResponse)
        assert result.data == {"config": cfg}


@pytest.mark.unit
class TestFormatStorageStrategyList:
    def test_constructs_expected_shape(self):
        svc, _ = _make_service()
        result = svc.format_storage_strategy_list(["dynamodb"], "dynamodb", 1)
        assert result.data == {
            "strategies": ["dynamodb"],
            "current_strategy": "dynamodb",
            "count": 1,
        }


@pytest.mark.unit
class TestFormatStorageConfig:
    def test_wraps_in_config_key(self):
        svc, _ = _make_service()
        result = svc.format_storage_config({"table": "requests"})
        assert result.data == {"config": {"table": "requests"}}


@pytest.mark.unit
class TestFormatSystemStatus:
    def test_dict_input(self):
        svc, scheduler = _make_service()
        scheduler.format_system_status_response.return_value = {"status": "healthy"}
        result = svc.format_system_status({"status": "healthy"})
        assert isinstance(result, InterfaceResponse)
        scheduler.format_system_status_response.assert_called_once_with({"status": "healthy"})

    def test_model_dump_input(self):
        svc, scheduler = _make_service()
        mock_status = MagicMock()
        mock_status.model_dump.return_value = {"status": "ok"}
        del mock_status.to_dict  # ensure model_dump branch taken
        scheduler.format_system_status_response.return_value = {"status": "ok"}
        result = svc.format_system_status(mock_status)
        mock_status.model_dump.assert_called_once()
        assert isinstance(result, InterfaceResponse)

    def test_to_dict_input(self):
        svc, scheduler = _make_service()

        class FakeStatus:
            def to_dict(self):
                return {"status": "degraded"}

        scheduler.format_system_status_response.return_value = {"status": "degraded"}
        result = svc.format_system_status(FakeStatus())
        scheduler.format_system_status_response.assert_called_once_with({"status": "degraded"})
        assert isinstance(result, InterfaceResponse)

    def test_unknown_type_converts_to_string(self):
        svc, scheduler = _make_service()
        scheduler.format_system_status_response.return_value = {"status": "unknown"}

        class PlainObj:
            pass

        svc.format_system_status(PlainObj())
        call_arg = scheduler.format_system_status_response.call_args[0][0]
        assert "status" in call_arg


@pytest.mark.unit
class TestFormatProviderDetail:
    def test_delegates_to_scheduler(self):
        svc, scheduler = _make_service()
        scheduler.format_provider_detail_response.return_value = {"provider": "aws", "type": "aws"}
        result = svc.format_provider_detail({"provider": "aws"})
        assert isinstance(result, InterfaceResponse)
        assert result.data["provider"] == "aws"


@pytest.mark.unit
class TestFormatStorageTest:
    def test_success_sets_exit_code_0(self):
        svc, scheduler = _make_service()
        scheduler.format_storage_test_response.return_value = {"success": True, "latency": 5}
        result = svc.format_storage_test({"success": True})
        assert result.exit_code == 0

    def test_failure_sets_exit_code_1(self):
        svc, scheduler = _make_service()
        scheduler.format_storage_test_response.return_value = {"success": False, "error": "timeout"}
        result = svc.format_storage_test({"success": False})
        assert result.exit_code == 1


@pytest.mark.unit
class TestFormatMachineOperation:
    def test_no_error_key_sets_exit_code_0(self):
        svc, scheduler = _make_service()
        scheduler.format_machine_details_response.return_value = {
            "machine_id": "m-1",
            "status": "running",
        }
        result = svc.format_machine_operation({"machine_id": "m-1"})
        assert result.exit_code == 0

    def test_error_key_sets_exit_code_1(self):
        svc, scheduler = _make_service()
        scheduler.format_machine_details_response.return_value = {
            "machine_id": "m-1",
            "error": "not found",
        }
        result = svc.format_machine_operation({"machine_id": "m-1"})
        assert result.exit_code == 1


@pytest.mark.unit
class TestFormatConfig:
    def test_returns_raw_dict_as_interface_response(self):
        svc, _ = _make_service()
        result = svc.format_config({"key": "value"})
        assert isinstance(result, InterfaceResponse)
        assert result.data == {"key": "value"}


@pytest.mark.unit
class TestFormatSuccess:
    def test_adds_success_flag_and_exit_code_0(self):
        svc, _ = _make_service()
        result = svc.format_success({"message": "done"})
        assert result.exit_code == 0
        assert result.data["success"] is True
        assert result.data["message"] == "done"


@pytest.mark.unit
class TestFormatError:
    def test_default_exit_code_1(self):
        svc, _ = _make_service()
        result = svc.format_error("something went wrong")
        assert result.exit_code == 1
        assert result.data["success"] is False
        assert result.data["error"] == "something went wrong"

    def test_custom_exit_code(self):
        svc, _ = _make_service()
        result = svc.format_error("bad config", exit_code=2)
        assert result.exit_code == 2
