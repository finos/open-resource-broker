"""Response formatting service — wraps SchedulerPort with explicit per-operation methods."""

from typing import Any

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.ports.scheduler_port import SchedulerPort


class ResponseFormattingService:
    def __init__(self, scheduler: SchedulerPort) -> None:
        self._scheduler = scheduler

    def format_request_operation(self, raw: dict[str, Any], status: str) -> InterfaceResponse:
        """Format a request creation/mutation result."""
        data = self._scheduler.format_request_response(raw)
        exit_code = self._scheduler.get_exit_code_for_status(status)
        return InterfaceResponse(data=data, exit_code=exit_code)

    def format_request_status(self, requests: list[Any]) -> InterfaceResponse:
        """Format a list of request status DTOs."""
        data = self._scheduler.format_request_status_response(requests)
        return InterfaceResponse(data=data)

    def format_machine_list(self, machines: list[Any]) -> InterfaceResponse:
        """Format a list of machine DTOs."""
        data = self._scheduler.format_machine_status_response(machines)
        return InterfaceResponse(data=data)

    def format_machine_detail(self, machine: dict[str, Any]) -> InterfaceResponse:
        """Format a single machine detail dict."""
        data = self._scheduler.format_machine_details_response(machine)
        return InterfaceResponse(data=data)

    def format_template_list(self, templates: list[Any]) -> InterfaceResponse:
        """Format a list of template DTOs."""
        data = self._scheduler.format_templates_response(templates)
        return InterfaceResponse(data=data)

    def format_template_mutation(self, raw: dict[str, Any]) -> InterfaceResponse:
        """Format a template create/update/delete/validate result."""
        data = self._scheduler.format_template_mutation_response(raw)
        return InterfaceResponse(data=data)

    def format_scheduler_strategy_list(
        self, strategies: list, current_strategy: str, count: int
    ) -> InterfaceResponse:
        """Format a scheduler strategies list."""
        data = {"strategies": strategies, "current_strategy": current_strategy, "count": count}
        return InterfaceResponse(data=data)

    def format_scheduler_config(self, config: dict) -> InterfaceResponse:
        """Format scheduler configuration."""
        data = {"config": config}
        return InterfaceResponse(data=data)

    def format_storage_strategy_list(
        self, strategies: list, current_strategy: str, count: int
    ) -> InterfaceResponse:
        """Format a storage strategies list."""
        data = {"strategies": strategies, "current_strategy": current_strategy, "count": count}
        return InterfaceResponse(data=data)

    def format_storage_config(self, config: dict) -> InterfaceResponse:
        """Format storage configuration."""
        data = {"config": config}
        return InterfaceResponse(data=data)

    def format_system_status(self, status: Any) -> InterfaceResponse:
        """Format system status (DTO or dict) for CLI display."""
        if hasattr(status, "model_dump"):
            raw = status.model_dump()
        elif hasattr(status, "to_dict"):
            raw = status.to_dict()
        elif isinstance(status, dict):
            raw = status
        else:
            raw = {"status": str(status)}
        data = self._scheduler.format_system_status_response(raw)
        return InterfaceResponse(data=data)

    def format_provider_detail(self, provider: dict[str, Any]) -> InterfaceResponse:
        """Format a provider detail dict for CLI display."""
        data = self._scheduler.format_provider_detail_response(provider)
        return InterfaceResponse(data=data)

    def format_storage_test(self, raw: dict[str, Any]) -> InterfaceResponse:
        """Format a storage test result for CLI display."""
        data = self._scheduler.format_storage_test_response(raw)
        exit_code = 0 if data.get("success") else 1
        return InterfaceResponse(data=data, exit_code=exit_code)

    def format_machine_operation(self, raw: dict[str, Any]) -> InterfaceResponse:
        """Format a machine stop/start operation result."""
        data = self._scheduler.format_machine_details_response(raw)
        exit_code = 0 if not data.get("error") else 1
        return InterfaceResponse(data=data, exit_code=exit_code)

    def format_config(self, raw: dict[str, Any]) -> InterfaceResponse:
        """Format a generic config/info dict as a successful response."""
        return InterfaceResponse(data=raw)

    def format_success(self, data: dict[str, Any]) -> InterfaceResponse:
        """Format a generic success response."""
        return InterfaceResponse(data={**data, "success": True}, exit_code=0)

    def format_error(self, message: str, exit_code: int = 1) -> InterfaceResponse:
        """Format an error response."""
        return InterfaceResponse(data={"success": False, "error": message}, exit_code=exit_code)
