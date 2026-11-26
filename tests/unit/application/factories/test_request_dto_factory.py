"""Unit tests for RequestDTOFactory."""

from datetime import datetime, timezone

import pytest

from orb.application.factories.request_dto_factory import RequestDTOFactory
from orb.application.request.dto import MachineReferenceDTO
from orb.domain.base.value_objects import InstanceType, Tags
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType
from orb.domain.request.value_objects import RequestId


@pytest.fixture
def factory():
    return RequestDTOFactory()


@pytest.fixture
def sample_request():
    return Request(
        request_id=RequestId(value="req-00000000-0000-0000-0000-000000000001"),
        request_type=RequestType.ACQUIRE,
        provider_type="aws",
        provider_name="aws_test",
        template_id="tmpl-1",
        requested_count=2,
        status="pending",
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_machine():
    return Machine(
        machine_id=MachineId(value="i-abc123"),
        template_id="tmpl-1",
        request_id="req-00000000-0000-0000-0000-000000000001",
        provider_type="aws",
        provider_name="aws_test",
        provider_api="ec2fleet",
        instance_type=InstanceType(value="t3.large"),
        image_id="ami-123",
        price_type="spot",
        status=MachineStatus.RUNNING,
        private_ip="10.0.1.1",
        launch_time=datetime.now(timezone.utc),
        metadata={"availability_zone": "eu-west-1a", "vcpus": 2},
    )


def sample_machine_with(provider_data):
    return Machine(
        machine_id=MachineId(value="i-abc123"),
        template_id="tmpl-1",
        request_id="req-00000000-0000-0000-0000-000000000001",
        provider_type="aws",
        provider_name="aws_test",
        provider_api="ec2fleet",
        instance_type=InstanceType(value="t3.large"),
        image_id="ami-123",
        status=MachineStatus.RUNNING,
        provider_data=provider_data,
    )


class TestMapMachineStatusToResultReturnRequest:
    def test_shutting_down_is_executing(self, factory):
        assert (
            factory.map_machine_status_to_result("shutting-down", RequestType.RETURN) == "executing"
        )

    def test_stopping_is_executing(self, factory):
        assert factory.map_machine_status_to_result("stopping", RequestType.RETURN) == "executing"

    def test_terminated_is_succeed(self, factory):
        assert factory.map_machine_status_to_result("terminated", RequestType.RETURN) == "succeed"

    def test_stopped_is_succeed(self, factory):
        assert factory.map_machine_status_to_result("stopped", RequestType.RETURN) == "succeed"

    def test_failed_is_fail(self, factory):
        assert factory.map_machine_status_to_result("failed", RequestType.RETURN) == "fail"

    def test_pending_is_executing(self, factory):
        assert factory.map_machine_status_to_result("pending", RequestType.RETURN) == "executing"


@pytest.mark.unit
class TestCreateFromDomainFields:
    """Verify new fields flow through create_from_domain to MachineReferenceDTO."""

    def test_instance_type_populated(self, factory, sample_request, sample_machine):
        dto = factory.create_from_domain(sample_request, [sample_machine])
        ref = dto.machine_references[0]
        assert ref.instance_type == "t3.large"

    def test_price_type_populated(self, factory, sample_request, sample_machine):
        dto = factory.create_from_domain(sample_request, [sample_machine])
        ref = dto.machine_references[0]
        assert ref.price_type == "spot"

    def test_placement_fields_read_from_provider_data(self, factory, sample_request):
        machine = sample_machine_with(provider_data={"availability_zone": "eu-west-1a", "vcpus": 2})
        ref = factory.create_from_domain(sample_request, [machine]).machine_references[0]
        assert ref.availability_zone == "eu-west-1a"
        assert ref.vcpus == 2

    def test_from_machine_reads_placement_fields(self):
        machine = sample_machine_with(provider_data={"availability_zone": "eu-west-1b", "vcpus": 4})
        ref = MachineReferenceDTO.from_machine(machine, RequestType.ACQUIRE)
        assert ref.availability_zone == "eu-west-1b"
        assert ref.vcpus == 4

    def test_placement_fields_are_not_serialised(self, factory, sample_request):
        # The watch view reads these; the request response format must not change.
        with_fields = sample_machine_with(
            provider_data={"availability_zone": "eu-west-1a", "vcpus": 2}
        )
        without_fields = sample_machine_with(provider_data={})
        dto_with = factory.create_from_domain(sample_request, [with_fields])
        dto_without = factory.create_from_domain(sample_request, [without_fields])
        ref_with = dto_with.machine_references[0]
        ref_without = dto_without.machine_references[0]
        assert ref_with.to_dict() == ref_without.to_dict()
        assert ref_with.model_dump_json() == ref_without.model_dump_json()
        assert "availability_zone" not in ref_with.to_dict()
        assert "vcpus" not in ref_with.to_dict()

    def test_missing_metadata_fields_are_none(self, factory, sample_request):
        machine = Machine(
            machine_id=MachineId(value="i-empty"),
            template_id="tmpl-1",
            request_id="req-test-001",
            provider_type="aws",
            provider_name="aws_test",
            provider_api="ec2fleet",
            instance_type=InstanceType(value="t3.micro"),
            image_id="ami-123",
            status=MachineStatus.PENDING,
            metadata={},
        )
        dto = factory.create_from_domain(sample_request, [machine])
        ref = dto.machine_references[0]
        assert ref.price_type is None


def _make_machine(**overrides) -> Machine:
    defaults = dict(
        machine_id=MachineId(value="i-1234567890abcdef0"),
        template_id="template-001",
        request_id="request-001",
        provider_type="aws",
        provider_name="aws-us-east-1",
        provider_api="ec2fleet",
        instance_type=InstanceType(value="m5.large"),
        image_id="ami-12345678",
        status=MachineStatus.RUNNING,
        price_type="ondemand",
        tags=Tags(tags={"Environment": "prod", "Owner": "team-x"}),
    )
    defaults.update(overrides)
    return Machine(**defaults)


def _make_request() -> Request:
    return Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="template-001",
        machine_count=1,
        provider_type="aws",
    )


class TestCreateFromDomainForwardsHFFields:
    """Regression: instance_type, price_type, tags must flow Machine -> DTO."""

    def test_populated_fields_forwarded(self, factory):
        machine = _make_machine()
        dto = factory.create_from_domain(_make_request(), [machine])

        assert len(dto.machine_references) == 1
        ref = dto.machine_references[0]
        assert ref.instance_type == "m5.large"
        assert ref.price_type == "ondemand"
        assert ref.tags == {"Environment": "prod", "Owner": "team-x"}

    def test_empty_tags_serialize_to_none(self, factory):
        machine = _make_machine(tags=Tags(tags={}))
        dto = factory.create_from_domain(_make_request(), [machine])

        assert dto.machine_references[0].tags is None

    def test_missing_price_type_forwarded_as_none(self, factory):
        machine = _make_machine(price_type=None)
        dto = factory.create_from_domain(_make_request(), [machine])

        assert dto.machine_references[0].price_type is None
