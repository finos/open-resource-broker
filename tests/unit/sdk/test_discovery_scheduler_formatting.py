"""
Tests for SDKMethodDiscovery scheduler formatting injection.

Verifies that:
- The correct format_* method is called for each known DTO type
- Lists of DTOs route through _apply_scheduler_format_list
- raw_response=True bypasses _standardize_return_type entirely
- No scheduler port degrades gracefully to raw to_dict() output
- Unknown DTO types return raw dict without calling any format_* method
- Formatter exceptions degrade gracefully to raw dict
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.sdk.discovery import MethodInfo, SDKMethodDiscovery


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_scheduler_port():
    port = MagicMock()
    port.format_request_for_display.return_value = {"requestId": "r-1", "status": "running"}
    port.format_request_status_response.return_value = {"requests": [{"requestId": "r-1"}]}
    port.format_request_response.return_value = {"requestId": "r-1", "message": "ok"}
    port.format_template_for_display.return_value = {"templateId": "t-1"}
    port.format_machine_details_response.return_value = {"machineId": "m-1"}
    return port


@pytest.fixture
def discovery_with_scheduler(mock_scheduler_port):
    return SDKMethodDiscovery(scheduler_port=mock_scheduler_port)


@pytest.fixture
def discovery_without_scheduler():
    return SDKMethodDiscovery()


def _make_dto(class_name: str, dict_data: dict) -> MagicMock:
    """Create a mock DTO with a given class name and to_dict return value."""
    dto = MagicMock()
    dto.to_dict.return_value = dict_data
    type(dto).__name__ = class_name
    return dto


# ---------------------------------------------------------------------------
# Single DTO dispatch
# ---------------------------------------------------------------------------


def test_request_dto_calls_format_request_for_display(discovery_with_scheduler, mock_scheduler_port):
    dto = _make_dto("RequestDTO", {"request_id": "r-1", "status": "in_progress"})

    result = discovery_with_scheduler._standardize_return_type(dto)

    mock_scheduler_port.format_request_for_display.assert_called_once()
    assert result == {"requestId": "r-1", "status": "running"}


def test_request_machines_response_calls_format_request_response(
    discovery_with_scheduler, mock_scheduler_port
):
    dto = _make_dto("RequestMachinesResponse", {"request_id": "r-1", "message": "ok"})

    result = discovery_with_scheduler._standardize_return_type(dto)

    mock_scheduler_port.format_request_response.assert_called_once()
    assert result == {"requestId": "r-1", "message": "ok"}


def test_return_request_response_calls_format_request_status_response(
    discovery_with_scheduler, mock_scheduler_port
):
    dto = _make_dto("ReturnRequestResponse", {"request_id": "r-1"})

    result = discovery_with_scheduler._standardize_return_type(dto)

    # expects_list=True for ReturnRequestResponse, so called with [dto]
    mock_scheduler_port.format_request_status_response.assert_called_once_with([dto])
    assert result == {"requests": [{"requestId": "r-1"}]}


def test_template_dto_calls_format_template_for_display(
    discovery_with_scheduler, mock_scheduler_port
):
    dto = _make_dto("TemplateDTO", {"template_id": "t-1"})

    result = discovery_with_scheduler._standardize_return_type(dto)

    mock_scheduler_port.format_template_for_display.assert_called_once()
    assert result == {"templateId": "t-1"}


def test_machine_dto_calls_format_machine_details_response(
    discovery_with_scheduler, mock_scheduler_port
):
    dto = _make_dto("MachineDTO", {"machine_id": "m-1"})

    result = discovery_with_scheduler._standardize_return_type(dto)

    mock_scheduler_port.format_machine_details_response.assert_called_once()
    assert result == {"machineId": "m-1"}


# ---------------------------------------------------------------------------
# List of DTOs dispatch
# ---------------------------------------------------------------------------


def test_list_of_request_dtos_calls_format_request_status_response(
    discovery_with_scheduler, mock_scheduler_port
):
    dto = _make_dto("RequestDTO", {"request_id": "r-1", "status": "complete"})

    result = discovery_with_scheduler._standardize_return_type([dto])

    # RequestDTO has expects_list=False, so list path calls formatter per item
    mock_scheduler_port.format_request_for_display.assert_called_once()
    # format_request_for_display returns {"requestId": "r-1", "status": "running"}
    assert result == [{"requestId": "r-1", "status": "running"}]


def test_list_of_request_status_response_dtos_passes_full_list(
    discovery_with_scheduler, mock_scheduler_port
):
    dto = _make_dto("RequestStatusResponse", {"request_id": "r-1"})

    result = discovery_with_scheduler._standardize_return_type([dto])

    # RequestStatusResponse has expects_list=True, so full list passed
    mock_scheduler_port.format_request_status_response.assert_called_once_with([dto])
    assert result == {"requests": [{"requestId": "r-1"}]}


# ---------------------------------------------------------------------------
# No scheduler port — graceful degradation
# ---------------------------------------------------------------------------


def test_no_scheduler_port_returns_raw_dict(discovery_without_scheduler):
    dto = _make_dto("RequestDTO", {"request_id": "r-1", "status": "in_progress"})

    result = discovery_without_scheduler._standardize_return_type(dto)

    assert result == {"request_id": "r-1", "status": "in_progress"}


def test_no_scheduler_port_list_returns_raw_list(discovery_without_scheduler):
    dto = _make_dto("RequestDTO", {"request_id": "r-1"})

    result = discovery_without_scheduler._standardize_return_type([dto])

    assert result == [{"request_id": "r-1"}]


# ---------------------------------------------------------------------------
# Unknown DTO type — no format_* called
# ---------------------------------------------------------------------------


def test_unknown_dto_type_returns_raw_dict(discovery_with_scheduler, mock_scheduler_port):
    dto = _make_dto("SomeUnknownDTO", {"some_field": "value"})

    result = discovery_with_scheduler._standardize_return_type(dto)

    mock_scheduler_port.format_request_for_display.assert_not_called()
    mock_scheduler_port.format_template_for_display.assert_not_called()
    assert result == {"some_field": "value"}


# ---------------------------------------------------------------------------
# Formatter exception — graceful degradation
# ---------------------------------------------------------------------------


def test_formatter_exception_falls_back_to_raw(discovery_with_scheduler, mock_scheduler_port):
    mock_scheduler_port.format_request_for_display.side_effect = RuntimeError("boom")
    dto = _make_dto("RequestDTO", {"request_id": "r-1"})

    result = discovery_with_scheduler._standardize_return_type(dto)

    assert result == {"request_id": "r-1"}


def test_list_formatter_exception_falls_back_to_raw_list(
    discovery_with_scheduler, mock_scheduler_port
):
    mock_scheduler_port.format_request_status_response.side_effect = RuntimeError("boom")
    dto = _make_dto("RequestStatusResponse", {"request_id": "r-1"})

    result = discovery_with_scheduler._standardize_return_type([dto])

    assert result == [{"request_id": "r-1"}]


# ---------------------------------------------------------------------------
# raw_response=True bypasses scheduler formatting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raw_response_bypasses_scheduler_formatting(mock_scheduler_port):
    """raw_response=True must return the DTO object before _standardize_return_type."""
    query_bus = AsyncMock()
    dto = _make_dto("RequestDTO", {"request_id": "r-1"})
    query_bus.execute.return_value = dto

    discovery = SDKMethodDiscovery(scheduler_port=mock_scheduler_port)

    method_info = MethodInfo(
        name="get_request",
        description="",
        parameters={},
        required_params=[],
        return_type=None,
        handler_type="query",
        original_class=MagicMock,
    )

    query_type = MagicMock()
    query_type.__name__ = "GetRequestQuery"
    query_type.return_value = MagicMock()

    sdk_method = discovery._create_query_method_cqrs(query_bus, query_type, method_info)

    result = await sdk_method(request_id="r-1", raw_response=True)

    mock_scheduler_port.format_request_for_display.assert_not_called()
    assert result is dto


# ---------------------------------------------------------------------------
# None result
# ---------------------------------------------------------------------------


def test_none_result_returns_none(discovery_with_scheduler):
    assert discovery_with_scheduler._standardize_return_type(None) is None


# ---------------------------------------------------------------------------
# Constructor default — no scheduler_port arg
# ---------------------------------------------------------------------------


def test_default_constructor_has_no_scheduler_port():
    d = SDKMethodDiscovery()
    assert d._scheduler_port is None


def test_constructor_stores_scheduler_port(mock_scheduler_port):
    d = SDKMethodDiscovery(scheduler_port=mock_scheduler_port)
    assert d._scheduler_port is mock_scheduler_port
