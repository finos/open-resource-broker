"""Unit tests for the stateless HostFactory status-mapper functions.

These functions are extracted from HostFactorySchedulerStrategy and are
pure input→output with no side effects, making them trivially testable
in isolation.
"""

from orb.infrastructure.scheduler.hostfactory.formatters.status_mapper import (
    generate_status_message,
    map_domain_status_to_hostfactory,
    map_machine_status_to_result,
)


class TestMapDomainStatusToHostfactory:
    """Tests for map_domain_status_to_hostfactory."""

    def test_pending_maps_to_running(self) -> None:
        assert map_domain_status_to_hostfactory("pending") == "running"

    def test_in_progress_maps_to_running(self) -> None:
        assert map_domain_status_to_hostfactory("in_progress") == "running"

    def test_provisioning_maps_to_running(self) -> None:
        assert map_domain_status_to_hostfactory("provisioning") == "running"

    def test_complete_maps_to_complete(self) -> None:
        assert map_domain_status_to_hostfactory("complete") == "complete"

    def test_completed_maps_to_complete(self) -> None:
        assert map_domain_status_to_hostfactory("completed") == "complete"

    def test_partial_maps_to_complete_with_error(self) -> None:
        assert map_domain_status_to_hostfactory("partial") == "complete_with_error"

    def test_failed_maps_to_complete_with_error(self) -> None:
        assert map_domain_status_to_hostfactory("failed") == "complete_with_error"

    def test_cancelled_maps_to_complete_with_error(self) -> None:
        assert map_domain_status_to_hostfactory("cancelled") == "complete_with_error"

    def test_timeout_maps_to_complete_with_error(self) -> None:
        assert map_domain_status_to_hostfactory("timeout") == "complete_with_error"

    def test_error_maps_to_complete_with_error(self) -> None:
        assert map_domain_status_to_hostfactory("error") == "complete_with_error"

    def test_unknown_status_defaults_to_running(self) -> None:
        assert map_domain_status_to_hostfactory("something_unknown") == "running"

    def test_case_insensitive(self) -> None:
        assert map_domain_status_to_hostfactory("FAILED") == "complete_with_error"
        assert map_domain_status_to_hostfactory("Complete") == "complete"


class TestMapMachineStatusToResult:
    """Tests for map_machine_status_to_result."""

    # Acquire / provision context (default)
    def test_running_is_succeed(self) -> None:
        assert map_machine_status_to_result("running") == "succeed"

    def test_pending_is_executing(self) -> None:
        assert map_machine_status_to_result("pending") == "executing"

    def test_launching_is_executing(self) -> None:
        assert map_machine_status_to_result("launching") == "executing"

    def test_terminated_is_fail_for_acquire(self) -> None:
        assert map_machine_status_to_result("terminated") == "fail"

    def test_failed_is_fail(self) -> None:
        assert map_machine_status_to_result("failed") == "fail"

    def test_error_is_fail(self) -> None:
        assert map_machine_status_to_result("error") == "fail"

    def test_none_status_defaults_to_executing(self) -> None:
        assert map_machine_status_to_result(None) == "executing"

    def test_unknown_status_defaults_to_executing(self) -> None:
        assert map_machine_status_to_result("some_weird_state") == "executing"

    # Return context
    def test_terminated_is_succeed_for_return(self) -> None:
        assert map_machine_status_to_result("terminated", request_type="return") == "succeed"

    def test_stopped_is_succeed_for_return(self) -> None:
        assert map_machine_status_to_result("stopped", request_type="return") == "succeed"

    def test_shutting_down_is_executing_for_return(self) -> None:
        assert map_machine_status_to_result("shutting-down", request_type="return") == "executing"

    def test_running_is_executing_for_return(self) -> None:
        assert map_machine_status_to_result("running", request_type="return") == "executing"

    def test_stopping_is_executing_for_return(self) -> None:
        assert map_machine_status_to_result("stopping", request_type="return") == "executing"

    def test_unknown_is_fail_for_return(self) -> None:
        assert map_machine_status_to_result("some_weird_state", request_type="return") == "fail"


class TestGenerateStatusMessage:
    """Tests for generate_status_message."""

    def test_completed_returns_empty_string(self) -> None:
        assert generate_status_message("completed", 5) == ""

    def test_partial_includes_machine_count(self) -> None:
        msg = generate_status_message("partial", 3)
        assert "3" in msg
        assert "Partially fulfilled" in msg

    def test_failed_returns_failure_message(self) -> None:
        msg = generate_status_message("failed", 0)
        assert "Failed" in msg

    def test_pending_returns_empty_string(self) -> None:
        assert generate_status_message("pending", 2) == ""

    def test_in_progress_returns_empty_string(self) -> None:
        assert generate_status_message("in_progress", 1) == ""

    def test_provisioning_returns_empty_string(self) -> None:
        assert generate_status_message("provisioning", 4) == ""

    def test_unknown_status_returns_empty_string(self) -> None:
        assert generate_status_message("some_unknown_state", 0) == ""
