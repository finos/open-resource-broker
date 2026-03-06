"""Status enum exhaustiveness contract tests.

Validates that every domain RequestStatus value ORB can emit maps to a
valid scheduler output status for both the HF and default schedulers.

These are pure unit tests — no I/O, no DI container, no moto.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

# ---------------------------------------------------------------------------
# Allowed output sets per scheduler (from plugin_io_schemas.py)
# ---------------------------------------------------------------------------

HF_ALLOWED_STATUSES = {
    "running",
    "complete",
    "complete_with_error",
    "failed",
    "partial",
    "cancelled",
    "timeout",
}

DEFAULT_ALLOWED_STATUSES = {
    "running",
    "complete",
    "complete_with_error",
}

HF_MACHINE_RESULTS = {"executing", "succeed", "fail"}
HF_MACHINE_STATUSES = {"pending", "running", "terminated", "failed", "error"}


# ---------------------------------------------------------------------------
# 1. HF: every domain RequestStatus maps to an allowed HF output status
# ---------------------------------------------------------------------------


def test_hf_all_domain_statuses_map_to_allowed_output(hf_strategy):
    """Every RequestStatus value maps to a value in the HF allowed status set."""
    from orb.domain.request.request_types import RequestStatus

    for domain_status in RequestStatus:
        mapped = hf_strategy._map_domain_status_to_hostfactory(domain_status.value)
        assert mapped in HF_ALLOWED_STATUSES, (
            f"RequestStatus.{domain_status.name} ('{domain_status.value}') "
            f"mapped to '{mapped}' which is NOT in HF allowed set {HF_ALLOWED_STATUSES}"
        )


def test_hf_status_mapping_covers_active_states(hf_strategy):
    """Active domain states (pending, in_progress, acquiring) map to 'running'."""
    active_states = ["pending", "in_progress", "acquiring"]
    for state in active_states:
        mapped = hf_strategy._map_domain_status_to_hostfactory(state)
        assert mapped == "running", (
            f"Active state '{state}' should map to 'running', got '{mapped}'"
        )


def test_hf_status_mapping_covers_terminal_states(hf_strategy):
    """Terminal domain states map to terminal HF statuses."""
    terminal_map = {
        "complete": "complete",
        "completed": "complete",
        "partial": "complete_with_error",
        "failed": "complete_with_error",
        "cancelled": "complete_with_error",
        "timeout": "complete_with_error",
        "error": "complete_with_error",
    }
    for domain_status, expected_hf in terminal_map.items():
        mapped = hf_strategy._map_domain_status_to_hostfactory(domain_status)
        assert mapped == expected_hf, (
            f"Domain status '{domain_status}' should map to '{expected_hf}', got '{mapped}'"
        )


# ---------------------------------------------------------------------------
# 2. HF: every machine status maps to an allowed result value
# ---------------------------------------------------------------------------


def test_hf_machine_status_to_result_acquire(hf_strategy):
    """Machine statuses for acquire requests map to valid HF result values."""
    cases = {
        "running": "succeed",
        "pending": "executing",
        "launching": "executing",
        "terminated": "fail",
        "failed": "fail",
        "error": "fail",
    }
    for machine_status, expected_result in cases.items():
        result = hf_strategy._map_machine_status_to_result(machine_status, request_type="acquire")
        assert result in HF_MACHINE_RESULTS, (
            f"machine status '{machine_status}' produced '{result}' not in {HF_MACHINE_RESULTS}"
        )
        assert result == expected_result, (
            f"machine status '{machine_status}' expected '{expected_result}', got '{result}'"
        )


def test_hf_machine_status_to_result_return(hf_strategy):
    """Machine statuses for return requests map to valid HF result values."""
    cases = {
        "terminated": "succeed",
        "stopped": "succeed",
        "shutting-down": "executing",
        "stopping": "executing",
        "pending": "executing",
    }
    for machine_status, expected_result in cases.items():
        result = hf_strategy._map_machine_status_to_result(machine_status, request_type="return")
        assert result in HF_MACHINE_RESULTS, (
            f"return machine status '{machine_status}' produced '{result}' not in {HF_MACHINE_RESULTS}"
        )
        assert result == expected_result, (
            f"return machine status '{machine_status}' expected '{expected_result}', got '{result}'"
        )


def test_hf_machine_status_to_result_unknown_is_safe(hf_strategy):
    """Unknown machine status produces a value in the allowed set (no KeyError)."""
    result = hf_strategy._map_machine_status_to_result("some-unknown-state")
    assert result in HF_MACHINE_RESULTS, (
        f"Unknown machine status produced '{result}' not in {HF_MACHINE_RESULTS}"
    )


# ---------------------------------------------------------------------------
# 3. Default scheduler: domain statuses map to allowed output statuses
# ---------------------------------------------------------------------------


def test_default_format_request_response_pending(default_strategy):
    """Default format_request_response for pending status returns request_id."""
    data = {
        "request_id": "req-00000000-0000-0000-0000-000000000001",
        "status": "pending",
    }
    response = default_strategy.format_request_response(data)
    assert "request_id" in response
    assert response["request_id"] == "req-00000000-0000-0000-0000-000000000001"


def test_default_format_request_response_all_statuses_have_request_id(default_strategy):
    """Default format_request_response always includes request_id for non-error statuses."""
    non_error_statuses = ["pending", "in_progress", "complete"]
    for status in non_error_statuses:
        data = {
            "request_id": "req-00000000-0000-0000-0000-000000000001",
            "status": status,
        }
        response = default_strategy.format_request_response(data)
        assert "request_id" in response, (
            f"Default response for status '{status}' missing 'request_id': {response}"
        )


# ---------------------------------------------------------------------------
# 4. Enum exhaustiveness: no domain status is unmapped
# ---------------------------------------------------------------------------


def test_hf_no_domain_status_raises_on_mapping(hf_strategy):
    """_map_domain_status_to_hostfactory never raises for any RequestStatus value."""
    from orb.domain.request.request_types import RequestStatus

    for domain_status in RequestStatus:
        try:
            result = hf_strategy._map_domain_status_to_hostfactory(domain_status.value)
            assert isinstance(result, str), (
                f"Mapping for '{domain_status.value}' returned non-string: {result!r}"
            )
        except Exception as exc:
            pytest.fail(
                f"_map_domain_status_to_hostfactory raised for '{domain_status.value}': {exc}"
            )


def test_hf_no_machine_status_raises_on_mapping(hf_strategy):
    """_map_machine_status_to_result never raises for any known machine status."""
    known_statuses = list(HF_MACHINE_STATUSES) + [
        "launching",
        "stopping",
        "shutting-down",
        "stopped",
    ]
    for status in known_statuses:
        try:
            result = hf_strategy._map_machine_status_to_result(status)
            assert result in HF_MACHINE_RESULTS
        except Exception as exc:
            pytest.fail(f"_map_machine_status_to_result raised for '{status}': {exc}")
