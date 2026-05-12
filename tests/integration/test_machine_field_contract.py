"""Contract test: Machine fields survive the full adapter → DTO → HF wire pipeline.

Guards against silent field-drop across:
  Machine aggregate → RequestDTOFactory → MachineReferenceDTO → HF formatter
"""

import json
from datetime import datetime, timezone

from orb.application.factories.request_dto_factory import RequestDTOFactory
from orb.domain.base.value_objects import InstanceType, Tags
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType as RequestTypeVO
from orb.infrastructure.scheduler.hostfactory.response_formatter import (
    HostFactoryResponseFormatter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request() -> Request:
    """Build a minimal ACQUIRE Request with a generated (valid) request ID."""
    return Request.create_new_request(
        request_type=RequestTypeVO.ACQUIRE,
        template_id="tmpl-contract",
        machine_count=1,
        provider_type="aws",
        provider_name="aws-default",
    )


def _make_machine(request_id: str) -> Machine:
    """Build a fully-populated Machine with every HF-visible field set."""
    return Machine(
        machine_id=MachineId(value="i-0abc123def456789"),
        name="contract-test-host",
        template_id="tmpl-contract",
        request_id=request_id,
        return_request_id="ret-return-001",
        provider_type="aws",
        provider_name="aws-default",
        instance_type=InstanceType(value="m5.xlarge"),
        image_id="ami-0deadbeef",
        price_type="spot",
        private_ip="10.0.1.42",
        public_ip="54.1.2.3",
        status=MachineStatus.RUNNING,
        launch_time=datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc),
        tags=Tags(tags={"Env": "test", "Owner": "contract"}),
        provider_data={"cloud_host_id": "cloud-host-abc"},
    )


# ---------------------------------------------------------------------------
# Contract test
# ---------------------------------------------------------------------------


class TestMachineFieldContract:
    """End-to-end contract: Machine → MachineReferenceDTO → HF wire format."""

    def setup_method(self):
        self.factory = RequestDTOFactory()
        self.formatter = HostFactoryResponseFormatter()

    def test_all_hf_fields_survive_machine_to_wire(self):
        """A fully populated Machine produces an HF response with all expected fields."""
        request = _make_request()
        request_id = str(request.request_id)
        machine = _make_machine(request_id)

        # --- Layer 1: Machine → RequestDTO (via MachineReferenceDTO) ---
        request_dto = self.factory.create_from_domain(request, [machine])

        assert len(request_dto.machine_references) == 1
        ref = request_dto.machine_references[0]

        # Verify DTO carries all HF-relevant fields before formatting
        assert ref.machine_id == "i-0abc123def456789"
        assert ref.name == "contract-test-host"
        assert ref.status == "running"
        assert ref.private_ip_address == "10.0.1.42"
        assert ref.launch_time == int(
            datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        )
        assert ref.instance_type == "m5.xlarge"
        assert ref.price_type == "spot"
        assert ref.tags is not None
        assert ref.tags == {"Env": "test", "Owner": "contract"}
        assert ref.cloud_host_id == "cloud-host-abc"

        # --- Layer 2: RequestDTO → HF wire via format_request_status_response ---
        wire = self.formatter.format_request_status_response(
            [request_dto],
            format_machines_fn=self.formatter.format_machines_for_hostfactory,
            map_status_fn=self.formatter.map_domain_status_to_hostfactory,
        )

        assert "requests" in wire
        assert len(wire["requests"]) == 1
        hf_request = wire["requests"][0]

        assert hf_request["requestId"] == request_id
        assert "machines" in hf_request
        assert len(hf_request["machines"]) == 1

        m = hf_request["machines"][0]

        # Core identity
        assert m["machineId"] == "i-0abc123def456789", "machineId dropped"
        assert m["name"] == "contract-test-host", "name dropped"

        # Status / result
        assert m["status"] == "running", "status dropped"
        assert m["result"] == "succeed", "result dropped (running → succeed for acquire)"

        # Network
        assert m["privateIpAddress"] == "10.0.1.42", "privateIpAddress dropped"

        # Timing
        expected_launchtime = int(datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc).timestamp())
        assert m["launchtime"] == expected_launchtime, "launchtime dropped"

        # Instance characteristics
        assert m.get("instanceType") == "m5.xlarge", "instanceType dropped"
        assert m.get("priceType") == "spot", "priceType dropped"

        # Tags — stored as JSON string in wire format
        assert "instanceTags" in m, "instanceTags dropped"
        wire_tags = json.loads(m["instanceTags"])
        assert wire_tags == {"Env": "test", "Owner": "contract"}, "instanceTags content wrong"

    def test_missing_optional_fields_do_not_appear_in_wire(self):
        """A Machine with no optional fields produces a clean wire entry without spurious keys."""
        request = _make_request()
        request_id = str(request.request_id)
        machine = Machine(
            machine_id=MachineId(value="i-minimal"),
            template_id="tmpl-contract",
            request_id=request_id,
            provider_type="aws",
            provider_name="aws-default",
            instance_type=InstanceType(value="t3.micro"),
            image_id="ami-0minimal",
            status=MachineStatus.PENDING,
        )

        request_dto = self.factory.create_from_domain(request, [machine])
        wire = self.formatter.format_request_status_response(
            [request_dto],
            format_machines_fn=self.formatter.format_machines_for_hostfactory,
            map_status_fn=self.formatter.map_domain_status_to_hostfactory,
        )

        m = wire["requests"][0]["machines"][0]

        # Required fields always present
        assert m["machineId"] == "i-minimal"
        assert m["status"] == "pending"
        assert m["result"] == "executing"

        # instance_type is required on Machine so it always appears in the wire output
        assert m.get("instanceType") == "t3.micro", (
            "instanceType should reflect machine instance_type"
        )

        # price_type and tags are truly optional — absent when not set on the Machine
        assert "priceType" not in m, "priceType should be absent when not set"
        assert "instanceTags" not in m, "instanceTags should be absent when not set"

    def test_tags_serialised_as_sorted_json_string(self):
        """Tags must be a JSON string (not a dict) in the wire format, sorted by key."""
        request = _make_request()
        request_id = str(request.request_id)
        machine = _make_machine(request_id)
        # Override tags with unsorted keys to verify sort_keys behaviour
        machine.tags = Tags(tags={"Zebra": "z", "Alpha": "a", "Middle": "m"})

        request_dto = self.factory.create_from_domain(request, [machine])

        # DTO layer: tags stored as dict
        ref = request_dto.machine_references[0]
        assert ref.tags == {"Zebra": "z", "Alpha": "a", "Middle": "m"}

        wire = self.formatter.format_request_status_response(
            [request_dto],
            format_machines_fn=self.formatter.format_machines_for_hostfactory,
            map_status_fn=self.formatter.map_domain_status_to_hostfactory,
        )
        m = wire["requests"][0]["machines"][0]
        assert isinstance(m["instanceTags"], str), "instanceTags must be a JSON string in wire"
        parsed = json.loads(m["instanceTags"])
        assert list(parsed.keys()) == sorted(parsed.keys()), "instanceTags keys must be sorted"
