"""Contract tests for HostFactory protocol response shapes.

These tests pin the exact JSON keys returned by the HF strategy's formatting
methods so that a field rename (e.g. request_id → requestId) is caught
immediately rather than discovered at runtime by an HF client.
"""

from unittest.mock import MagicMock

from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.scheduler.hostfactory.response_formatter import (
    HostFactoryResponseFormatter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy() -> HostFactorySchedulerStrategy:
    """Return a strategy with all external dependencies mocked out."""
    strategy = HostFactorySchedulerStrategy.__new__(HostFactorySchedulerStrategy)
    strategy._template_defaults_service = None
    strategy._field_mapper = None
    strategy._config_manager = None
    strategy._provider_registry_service = None
    strategy._logger = MagicMock()
    return strategy


# ---------------------------------------------------------------------------
# format_request_response (requestMachines / requestReturnMachines)
# ---------------------------------------------------------------------------


class TestRequestResponseShape:
    """format_request_response must use camelCase HF keys."""

    def setup_method(self):
        self.strategy = _make_strategy()

    def test_pending_response_has_requestId_not_request_id(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-abc", "status": "pending"}
        )
        assert "requestId" in result, "response must contain 'requestId'"
        assert "request_id" not in result, "response must NOT contain snake_case 'request_id'"

    def test_pending_response_has_message(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-abc", "status": "pending"}
        )
        assert "message" in result

    def test_failed_response_has_requestId_and_message(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-abc", "status": "failed", "status_message": "quota exceeded"}
        )
        assert "requestId" in result
        assert "message" in result
        # Must NOT use the old error/error_message keys
        assert "error" not in result
        assert "error_message" not in result

    def test_failed_response_message_contains_status_message(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-abc", "status": "failed", "status_message": "quota exceeded"}
        )
        assert "quota exceeded" in result["message"]

    def test_failed_response_without_status_message_has_fallback(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-abc", "status": "failed"}
        )
        assert result["message"]  # non-empty fallback

    def test_complete_response_shape(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-abc", "status": "complete"}
        )
        assert "requestId" in result
        assert "message" in result

    def test_cancelled_response_shape(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-abc", "status": "cancelled"}
        )
        assert "requestId" in result
        assert "message" in result

    def test_timeout_response_shape(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-abc", "status": "timeout"}
        )
        assert "requestId" in result
        assert "message" in result

    def test_partial_response_shape(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-abc", "status": "partial"}
        )
        assert "requestId" in result
        assert "message" in result

    def test_in_progress_response_shape(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-abc", "status": "in_progress"}
        )
        assert "requestId" in result
        assert "message" in result

    def test_request_id_value_is_preserved(self):
        result = self.strategy.format_request_response(
            {"request_id": "req-xyz-123", "status": "pending"}
        )
        assert result["requestId"] == "req-xyz-123"


# ---------------------------------------------------------------------------
# convert_domain_to_hostfactory_output — requestMachines
# ---------------------------------------------------------------------------


class TestRequestMachinesOutputShape:
    """convert_domain_to_hostfactory_output('requestMachines') contract."""

    def setup_method(self):
        self.strategy = _make_strategy()

    def test_string_input_returns_requestId(self):
        result = self.strategy.convert_domain_to_hostfactory_output("requestMachines", "req-abc")
        assert "requestId" in result
        assert "message" in result

    def test_dict_input_returns_requestId(self):
        result = self.strategy.convert_domain_to_hostfactory_output(
            "requestMachines", {"request_id": "req-abc", "resource_ids": ["fleet-1"]}
        )
        assert "requestId" in result
        assert "message" in result

    def test_no_snake_case_request_id_in_output(self):
        result = self.strategy.convert_domain_to_hostfactory_output(
            "requestMachines", {"request_id": "req-abc"}
        )
        assert "request_id" not in result


# ---------------------------------------------------------------------------
# convert_domain_to_hostfactory_output — getRequestStatus
# ---------------------------------------------------------------------------


class TestGetRequestStatusOutputShape:
    """convert_domain_to_hostfactory_output('getRequestStatus') contract."""

    def setup_method(self):
        self.strategy = _make_strategy()

    def _make_request_dto(self, request_id: str, status: str) -> MagicMock:
        dto = MagicMock()
        dto.request_id = request_id
        dto.status = status
        dto.to_dict.return_value = {
            "request_id": request_id,
            "status": status,
            "machines": [],
            "request_type": "provision",
        }
        return dto

    def test_status_response_has_requests_list(self):
        dto = self._make_request_dto("req-abc", "completed")
        result = self.strategy.convert_domain_to_hostfactory_output("getRequestStatus", dto)
        assert "requests" in result
        assert isinstance(result["requests"], list)

    def test_each_request_entry_has_requestId(self):
        dto = self._make_request_dto("req-abc", "completed")
        result = self.strategy.convert_domain_to_hostfactory_output("getRequestStatus", dto)
        entry = result["requests"][0]
        assert "requestId" in entry
        assert "request_id" not in entry

    def test_each_request_entry_has_status(self):
        dto = self._make_request_dto("req-abc", "completed")
        result = self.strategy.convert_domain_to_hostfactory_output("getRequestStatus", dto)
        entry = result["requests"][0]
        assert "status" in entry

    def test_each_request_entry_has_machines_list(self):
        dto = self._make_request_dto("req-abc", "completed")
        result = self.strategy.convert_domain_to_hostfactory_output("getRequestStatus", dto)
        entry = result["requests"][0]
        assert "machines" in entry
        assert isinstance(entry["machines"], list)

    def test_each_request_entry_has_message(self):
        dto = self._make_request_dto("req-abc", "completed")
        result = self.strategy.convert_domain_to_hostfactory_output("getRequestStatus", dto)
        entry = result["requests"][0]
        assert "message" in entry

    def test_status_is_mapped_to_hf_values(self):
        """Domain 'completed' must map to HF 'complete', not pass through raw."""
        dto = self._make_request_dto("req-abc", "completed")
        result = self.strategy.convert_domain_to_hostfactory_output("getRequestStatus", dto)
        entry = result["requests"][0]
        assert entry["status"] in {"running", "complete", "complete_with_error"}

    def test_dict_fallback_path_has_requestId(self):
        data = {
            "request_id": "req-dict",
            "status": "pending",
            "machines": [],
            "request_type": "provision",
        }
        result = self.strategy.convert_domain_to_hostfactory_output("getRequestStatus", data)
        entry = result["requests"][0]
        assert "requestId" in entry
        assert "request_id" not in entry


# ---------------------------------------------------------------------------
# format_request_status_response (list of RequestDTOs)
# ---------------------------------------------------------------------------


class TestFormatRequestStatusResponseShape:
    """format_request_status_response must produce HF-compliant request objects."""

    def setup_method(self):
        self.strategy = _make_strategy()

    def _make_dto(self, request_id: str, status: str = "pending") -> MagicMock:
        dto = MagicMock()
        dto.to_dict.return_value = {
            "request_id": request_id,
            "status": status,
            "machines": [],
            "message": "",
        }
        return dto

    def test_top_level_key_is_requests(self):
        result = self.strategy.format_request_status_response([self._make_dto("req-1")])
        assert "requests" in result

    def test_entry_has_requestId_not_request_id(self):
        result = self.strategy.format_request_status_response([self._make_dto("req-1")])
        entry = result["requests"][0]
        assert "requestId" in entry
        assert "request_id" not in entry

    def test_entry_has_status_and_machines(self):
        result = self.strategy.format_request_status_response([self._make_dto("req-1")])
        entry = result["requests"][0]
        assert "status" in entry
        assert "machines" in entry


# ---------------------------------------------------------------------------
# HostFactoryResponseFormatter (standalone formatter class)
# ---------------------------------------------------------------------------


class TestResponseFormatterShape:
    """HostFactoryResponseFormatter must produce the same HF-compliant shapes."""

    def setup_method(self):
        self.formatter = HostFactoryResponseFormatter()

    def _unwrap(self, raw_id):
        return raw_id if isinstance(raw_id, str) else str(raw_id)

    def _coerce(self, data):
        return data if isinstance(data, dict) else {}

    def test_failed_request_has_requestId_and_message(self):
        result = self.formatter.format_request_response(
            {"request_id": "req-abc", "status": "failed", "status_message": "out of capacity"},
            unwrap_id_fn=self._unwrap,
            coerce_fn=self._coerce,
        )
        assert "requestId" in result
        assert "message" in result
        assert "error" not in result
        assert "error_message" not in result

    def test_pending_request_has_requestId_and_message(self):
        result = self.formatter.format_request_response(
            {"request_id": "req-abc", "status": "pending"},
            unwrap_id_fn=self._unwrap,
            coerce_fn=self._coerce,
        )
        assert "requestId" in result
        assert "message" in result

    def test_get_request_status_dto_path_has_requestId(self):
        dto = MagicMock()
        dto.request_id = "req-abc"
        dto.status = "completed"
        dto.to_dict.return_value = {
            "request_id": "req-abc",
            "status": "completed",
            "machines": [],
            "request_type": "provision",
        }

        formatter = HostFactoryResponseFormatter()
        result = formatter.format_get_request_status(
            data=dto,
            format_machines_fn=lambda machines, request_type=None: [],
            map_status_fn=lambda s: "complete",
            generate_message_fn=lambda s, n: "",
        )
        entry = result["requests"][0]
        assert "requestId" in entry
        assert "request_id" not in entry

    def test_format_request_status_response_entry_shape(self):
        dto = MagicMock()
        dto.to_dict.return_value = {
            "request_id": "req-abc",
            "status": "pending",
            "machines": [],
            "message": "",
        }
        result = self.formatter.format_request_status_response(
            requests=[dto],
            format_machines_fn=lambda machines, request_type=None: [],
            map_status_fn=lambda s: "running",
        )
        entry = result["requests"][0]
        assert "requestId" in entry
        assert "request_id" not in entry
        assert "status" in entry
        assert "machines" in entry
