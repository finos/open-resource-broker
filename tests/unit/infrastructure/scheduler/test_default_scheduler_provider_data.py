"""Tests for DefaultSchedulerStrategy provider_data and status-check field surfacing."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy


@pytest.fixture
def strategy():
    return DefaultSchedulerStrategy()


# ---------------------------------------------------------------------------
# format_request_status_response — status-check fields
# ---------------------------------------------------------------------------


def _make_request_dto(extra: dict) -> MagicMock:
    dto = MagicMock()
    base = {
        "request_id": "req-1",
        "status": "complete",
        "requested_count": 1,
        "created_at": "2024-01-01T00:00:00",
        "machines": [],
    }
    base.update(extra)
    dto.to_dict.return_value = base
    return dto


class TestFormatRequestStatusFirstLastCheck:
    def test_includes_first_status_check_as_iso_string(self, strategy):
        dt = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        dto = _make_request_dto({"first_status_check": dt})
        result = strategy.format_request_status_response([dto])
        assert result["requests"][0]["first_status_check"] == dt.isoformat()

    def test_includes_last_status_check_as_iso_string(self, strategy):
        dt = datetime(2024, 6, 2, 8, 30, 0, tzinfo=timezone.utc)
        dto = _make_request_dto({"last_status_check": dt})
        result = strategy.format_request_status_response([dto])
        assert result["requests"][0]["last_status_check"] == dt.isoformat()

    def test_omits_first_status_check_when_none(self, strategy):
        dto = _make_request_dto({})
        result = strategy.format_request_status_response([dto])
        # None values are excluded by to_dict(verbose=True) via exclude_none
        assert (
            result["requests"][0].get("first_status_check") is None
            or "first_status_check" not in result["requests"][0]
        )

    def test_omits_last_status_check_when_none(self, strategy):
        dto = _make_request_dto({})
        result = strategy.format_request_status_response([dto])
        assert (
            result["requests"][0].get("last_status_check") is None
            or "last_status_check" not in result["requests"][0]
        )

    def test_started_at_serialized_as_iso_string(self, strategy):
        dt = datetime(2024, 5, 10, 9, 0, 0)
        dto = _make_request_dto({"started_at": dt})
        result = strategy.format_request_status_response([dto])
        assert result["requests"][0]["started_at"] == dt.isoformat()

    def test_completed_at_serialized_as_iso_string(self, strategy):
        dt = datetime(2024, 5, 10, 10, 0, 0)
        dto = _make_request_dto({"completed_at": dt})
        result = strategy.format_request_status_response([dto])
        assert result["requests"][0]["completed_at"] == dt.isoformat()

    def test_already_string_datetimes_pass_through_unchanged(self, strategy):
        dto = _make_request_dto({"started_at": "2024-05-10T09:00:00"})
        result = strategy.format_request_status_response([dto])
        assert result["requests"][0]["started_at"] == "2024-05-10T09:00:00"

    def test_count_field_present(self, strategy):
        dto = _make_request_dto({})
        result = strategy.format_request_status_response([dto])
        assert result["count"] == 1

    def test_empty_list(self, strategy):
        result = strategy.format_request_status_response([])
        assert result["requests"] == []
        assert result["count"] == 0

    def test_dict_input_passes_through(self, strategy):
        d = {"request_id": "req-dict", "status": "pending"}
        result = strategy.format_request_status_response([d])
        assert result["requests"][0]["request_id"] == "req-dict"


# ---------------------------------------------------------------------------
# format_machine_status_response — provider_data fields
# ---------------------------------------------------------------------------


def _make_machine_dto(provider_data: dict, top_level: dict | None = None) -> MagicMock:
    """Build a MagicMock that looks like a MachineDTO (has model_dump)."""
    dto = MagicMock(spec=[])  # no spec so model_dump is accessible
    base = {
        "machine_id": "i-abc123",
        "name": "test-host",
        "status": "running",
        "instance_type": "t3.medium",
        "private_ip": "10.0.0.1",
        "result": "executing",
        "provider_data": provider_data,
    }
    if top_level:
        base.update(top_level)
    dto.model_dump = MagicMock(return_value=base)
    return dto


class TestFormatMachineStatusProviderData:
    def test_includes_region_from_provider_data(self, strategy):
        dto = _make_machine_dto({"region": "us-east-1"})
        result = strategy.format_machine_status_response([dto])
        assert result["machines"][0]["region"] == "us-east-1"

    def test_includes_availability_zone_from_provider_data(self, strategy):
        dto = _make_machine_dto({"availability_zone": "us-east-1a"})
        result = strategy.format_machine_status_response([dto])
        assert result["machines"][0]["availability_zone"] == "us-east-1a"

    def test_includes_vcpus_from_provider_data(self, strategy):
        dto = _make_machine_dto({"vcpus": 4})
        result = strategy.format_machine_status_response([dto])
        assert result["machines"][0]["vcpus"] == 4

    def test_includes_health_checks_when_present(self, strategy):
        hc = {"system": "ok", "instance": "ok"}
        dto = _make_machine_dto({"health_checks": hc})
        result = strategy.format_machine_status_response([dto])
        assert result["machines"][0]["health_checks"] == hc

    def test_includes_cloud_host_id_from_provider_data(self, strategy):
        dto = _make_machine_dto({"cloud_host_id": "i-ec2-host"})
        result = strategy.format_machine_status_response([dto])
        assert result["machines"][0]["cloud_host_id"] == "i-ec2-host"

    def test_top_level_cloud_host_id_takes_precedence(self, strategy):
        dto = _make_machine_dto(
            {"cloud_host_id": "from-provider-data"},
            top_level={"cloud_host_id": "from-top-level"},
        )
        result = strategy.format_machine_status_response([dto])
        assert result["machines"][0]["cloud_host_id"] == "from-top-level"

    def test_omits_provider_data_fields_when_absent(self, strategy):
        dto = _make_machine_dto({})
        result = strategy.format_machine_status_response([dto])
        machine = result["machines"][0]
        assert "region" not in machine
        assert "availability_zone" not in machine
        assert "vcpus" not in machine
        assert "health_checks" not in machine

    def test_count_matches_input_length(self, strategy):
        dtos = [_make_machine_dto({"region": "us-west-2"}) for _ in range(3)]
        result = strategy.format_machine_status_response(dtos)
        assert result["count"] == 3

    def test_tags_remain_dict_not_json_string(self, strategy):
        tags = {"env": "prod", "team": "platform"}
        dto = _make_machine_dto({}, top_level={"tags": tags})
        result = strategy.format_machine_status_response([dto])
        assert isinstance(result["machines"][0]["tags"], dict)
        assert result["machines"][0]["tags"] == tags

    def test_datetimes_serialized_as_iso_strings_via_model_dump(self, strategy):
        # model_dump(mode="json") converts datetimes to strings automatically
        dt_str = "2024-03-15T10:00:00"
        dto = _make_machine_dto({}, top_level={"launch_time": dt_str})
        result = strategy.format_machine_status_response([dto])
        assert result["machines"][0]["launch_time"] == dt_str

    def test_empty_list(self, strategy):
        result = strategy.format_machine_status_response([])
        assert result["machines"] == []
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# format_machine_details_response — provider_data fields
# ---------------------------------------------------------------------------


class TestFormatMachineDetailsProviderData:
    def test_includes_region_from_provider_data(self, strategy):
        data = {"name": "h1", "status": "running", "provider_data": {"region": "eu-west-1"}}
        result = strategy.format_machine_details_response(data)
        assert result["region"] == "eu-west-1"

    def test_includes_availability_zone_from_provider_data(self, strategy):
        data = {"provider_data": {"availability_zone": "eu-west-1b"}}
        result = strategy.format_machine_details_response(data)
        assert result["availability_zone"] == "eu-west-1b"

    def test_includes_vcpus_from_provider_data(self, strategy):
        data = {"provider_data": {"vcpus": 8}}
        result = strategy.format_machine_details_response(data)
        assert result["vcpus"] == 8

    def test_includes_health_checks_from_provider_data(self, strategy):
        hc = {"system": "ok"}
        data = {"provider_data": {"health_checks": hc}}
        result = strategy.format_machine_details_response(data)
        assert result["health_checks"] == hc

    def test_includes_cloud_host_id_from_provider_data(self, strategy):
        data = {"provider_data": {"cloud_host_id": "i-host"}}
        result = strategy.format_machine_details_response(data)
        assert result["cloud_host_id"] == "i-host"

    def test_top_level_region_takes_precedence_over_provider_data(self, strategy):
        data = {"region": "ap-southeast-1", "provider_data": {"region": "us-east-1"}}
        result = strategy.format_machine_details_response(data)
        assert result["region"] == "ap-southeast-1"

    def test_omits_none_fields(self, strategy):
        data = {"name": "h1", "provider_data": {}}
        result = strategy.format_machine_details_response(data)
        # None-valued keys must not appear
        for v in result.values():
            assert v is not None

    def test_no_provider_data_key_does_not_raise(self, strategy):
        data = {"name": "h1", "status": "running"}
        result = strategy.format_machine_details_response(data)
        assert result["name"] == "h1"
        assert result["status"] == "running"
