"""Tests for MachineSyncService._create_machine_from_processed_data — tags passthrough."""

from unittest.mock import MagicMock

from orb.application.services.machine_sync_service import MachineSyncService
from orb.domain.base.value_objects import Tags
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType


def _make_service() -> MachineSyncService:
    return MachineSyncService(
        command_bus=MagicMock(),
        uow_factory=MagicMock(),
        config_port=MagicMock(),
        logger=MagicMock(),
        provider_registry_service=MagicMock(),
    )


def _make_request() -> Request:
    return Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl-1",
        machine_count=1,
        provider_type="aws",
        provider_name="aws-us-east-1",
    )


def _base_processed_data() -> dict:
    return {
        "instance_id": "i-0abc",
        "status": "running",
        "instance_type": "m5.large",
        "image_id": "ami-1",
        "price_type": "ondemand",
    }


class TestCreateMachineTagsPassthrough:
    def test_top_level_tags_populate_machine(self):
        svc = _make_service()
        data = _base_processed_data()
        data["tags"] = {"Environment": "prod", "Owner": "team-x"}

        machine = svc._create_machine_from_processed_data(data, _make_request())

        assert isinstance(machine.tags, Tags)
        assert machine.tags.tags == {"Environment": "prod", "Owner": "team-x"}

    def test_metadata_tags_are_ignored(self):
        """Legacy machine_adapter writes tags under metadata.tags; that path
        does not round-trip through storage and must NOT populate Machine.tags."""
        svc = _make_service()
        data = _base_processed_data()
        data["metadata"] = {"tags": {"Environment": "staging"}}

        machine = svc._create_machine_from_processed_data(data, _make_request())

        assert machine.tags.tags == {}

    def test_no_tags_yields_empty_tags(self):
        svc = _make_service()
        data = _base_processed_data()

        machine = svc._create_machine_from_processed_data(data, _make_request())

        assert machine.tags.tags == {}

    def test_price_type_forwarded(self):
        svc = _make_service()
        data = _base_processed_data()
        data["price_type"] = "spot"

        machine = svc._create_machine_from_processed_data(data, _make_request())

        assert machine.price_type == "spot"
