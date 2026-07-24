"""Response formatting service — wraps SchedulerPort with explicit per-operation methods."""

from typing import Any, Final

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.ports.scheduler_port import SchedulerPort


class _Unset:
    """Sentinel type marking a pagination argument the caller did not supply.

    Distinguishes a *LIST* response (caller passes ``total_count``/``next_cursor``
    — even when their value is ``None`` on the last page) from a *single-item* or
    HostFactory ``getRequestStatus`` response (caller omits them entirely). Only
    LIST callers get the pagination fields stamped into the payload; single/HF
    responses must stay free of ``next_cursor``/``total_count`` because the IBM
    Symphony HostFactory wire spec has no pagination cursor there.
    """


_UNSET: Final = _Unset()


class ResponseFormattingService:
    def __init__(self, scheduler: SchedulerPort) -> None:
        self._scheduler = scheduler

    @staticmethod
    def _stamp_pagination(
        data: Any,
        total_count: int | None | _Unset,
        next_cursor: str | None | _Unset,
    ) -> None:
        """Stamp pagination fields onto a LIST payload, in place.

        A field is written only when the caller *supplied* it (i.e. the argument
        is not the ``_UNSET`` sentinel). This gates pagination to LIST responses:
        single-item and HostFactory ``getRequestStatus`` callers omit both
        arguments, so their payload never gains ``next_cursor``/``total_count``.
        Supplying an explicit ``None`` (a last page with no more rows) still
        writes the key — LIST consumers such as the UI ``load-more`` control read
        ``res["next_cursor"]`` and rely on the key being present.
        """
        if not isinstance(data, dict):
            return
        if not isinstance(total_count, _Unset):
            data["total_count"] = total_count
        if not isinstance(next_cursor, _Unset):
            data["next_cursor"] = next_cursor

    def format_request_operation(self, raw: dict[str, Any], status: str) -> InterfaceResponse:
        """Format a request creation/mutation result."""
        data = self._scheduler.format_request_response(raw)
        exit_code = self._scheduler.get_exit_code_for_status(status)
        return InterfaceResponse(data=data, exit_code=exit_code)

    def format_request_status(
        self,
        requests: list[Any],
        *,
        total_count: int | None | _Unset = _UNSET,
        next_cursor: str | None | _Unset = _UNSET,
    ) -> InterfaceResponse:
        """Format request status DTOs for LIST *and* single/HF status responses.

        This method backs two distinct wire contracts sharing one formatter:

        * **LIST responses** (``list_requests`` CLI, ``GET /api/v1/requests/``)
          pass ``total_count`` and ``next_cursor`` so the paginated shape carries
          the load-more cursor.
        * **Single-request / HostFactory ``getRequestStatus`` responses**
          (``get_request_status`` CLI, ``GET /{id}``, ``GET /{id}/status``,
          ``POST /status``, SSE stream) omit both arguments. The IBM Symphony HF
          wire spec has no pagination cursor there, so ``next_cursor``/
          ``total_count`` must NOT be injected into that payload.

        Injection is therefore gated on whether the caller supplied the
        arguments, not on their value — a LIST last page still stamps
        ``next_cursor=None`` (key present for the UI), while a single/HF call
        leaves the payload clean.
        """
        data = self._scheduler.format_request_status_response(requests)
        self._stamp_pagination(data, total_count, next_cursor)
        return InterfaceResponse(data=data)

    def format_return_requests(self, requests: list[Any]) -> InterfaceResponse:
        """Format a list of return-request items using the scheduler's dedicated
        return-requests formatter — NOT format_request_status (different spec shape)."""
        data = self._scheduler.format_return_requests_response(requests)
        return InterfaceResponse(data=data)

    def format_machine_list(
        self,
        machines: list[Any],
        *,
        total_count: int | None | _Unset = _UNSET,
        next_cursor: str | None | _Unset = _UNSET,
    ) -> InterfaceResponse:
        """Format a LIST of machine DTOs.

        The ``total_count`` and ``next_cursor`` keyword arguments are stamped
        into the payload only when supplied, so the paginated CLI shape matches
        ``GET /api/v1/machines/``. A single-machine listing (no kwargs) stays
        free of pagination fields.
        """
        data = self._scheduler.format_machine_status_response(machines)
        self._stamp_pagination(data, total_count, next_cursor)
        return InterfaceResponse(data=data)

    def format_machine_detail(self, machine: dict[str, Any]) -> InterfaceResponse:
        """Format a single machine detail dict."""
        data = self._scheduler.format_machine_details_response(machine)
        return InterfaceResponse(data=data)

    def format_template_list(
        self,
        templates: list[Any],
        *,
        total_count: int | None | _Unset = _UNSET,
        next_cursor: str | None | _Unset = _UNSET,
    ) -> InterfaceResponse:
        """Format a LIST of template DTOs.

        The ``total_count`` and ``next_cursor`` keyword arguments are stamped
        into the payload only when supplied, so the paginated CLI shape matches
        ``GET /api/v1/templates/``. A refresh/list call without pagination
        kwargs stays free of pagination fields.
        """
        data = self._scheduler.format_templates_response(templates)
        self._stamp_pagination(data, total_count, next_cursor)
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
