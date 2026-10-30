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
        provider_api="ec2fleet",
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

    def test_status_reason_forwarded(self):
        """status_reason from the provider dict must be set on the created machine.

        Without this, a failed pod's error message is invisible on the first
        get-request-status poll — it only appears once a subsequent sync cycle
        triggers the update path.
        """
        svc = _make_service()
        data = _base_processed_data()
        data["status"] = "failed"
        data["status_reason"] = "OOMKilled"

        machine = svc._create_machine_from_processed_data(data, _make_request())

        assert machine.status_reason == "OOMKilled"

    def test_status_reason_absent_yields_none(self):
        """When the provider does not supply status_reason, the field stays None."""
        svc = _make_service()
        data = _base_processed_data()

        machine = svc._create_machine_from_processed_data(data, _make_request())

        assert machine.status_reason is None


class TestVpcIdIsCarriedEndToEnd:
    """The VPC id must survive creation, storage round trip and every output shape."""

    def _machine(self):
        data = _base_processed_data()
        data["vpc_id"] = "vpc-0abc"
        data["subnet_id"] = "subnet-1"
        return _make_service()._create_machine_from_processed_data(data, _make_request())

    def test_created_machine_keeps_vpc_id(self):
        assert self._machine().vpc_id == "vpc-0abc"

    def test_storage_round_trip_keeps_vpc_id(self):
        from orb.domain.machine.aggregate import Machine

        restored = Machine.model_validate(self._machine().model_dump(mode="json"))
        assert restored.vpc_id == "vpc-0abc"

    def test_machine_dto_and_default_scheduler_output_include_vpc_id(self):
        from orb.application.machine.dto import MachineDTO
        from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

        dto = MachineDTO.from_domain(self._machine())
        assert dto.vpc_id == "vpc-0abc"

        strategy = DefaultSchedulerStrategy.__new__(DefaultSchedulerStrategy)
        out = strategy.format_machine_status_response([dto])
        assert out["machines"][0]["vpc_id"] == "vpc-0abc"

    def test_request_machine_reference_includes_vpc_id(self):
        from orb.application.request.dto import MachineReferenceDTO

        ref = MachineReferenceDTO.from_machine(self._machine(), RequestType.ACQUIRE)
        assert ref.to_dict()["vpc_id"] == "vpc-0abc"

    def test_rest_machine_item_exposes_vpc_id(self):
        from orb.api.models.responses import MachineItem

        item = MachineItem.model_validate({"machine_id": "i-1", "vpc_id": "vpc-0abc"})
        assert item.model_dump()["vpc_id"] == "vpc-0abc"

    def test_missing_vpc_id_is_omitted_from_default_output(self):
        from orb.application.machine.dto import MachineDTO
        from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

        machine = _make_service()._create_machine_from_processed_data(
            _base_processed_data(), _make_request()
        )
        strategy = DefaultSchedulerStrategy.__new__(DefaultSchedulerStrategy)
        out = strategy.format_machine_status_response([MachineDTO.from_domain(machine)])
        assert "vpc_id" not in out["machines"][0]
