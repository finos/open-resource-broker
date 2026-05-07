"""Unit tests for MachineDTO.from_domain field mapping."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from orb.application.machine.dto import MachineDTO
from orb.domain.base.value_objects import InstanceType
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.value_objects import MachineStatus


def _make_machine(**overrides) -> Machine:
    defaults = {
        "machine_id": MachineId(value="i-test001"),
        "name": "test-machine",
        "status": MachineStatus.RUNNING,
        "instance_type": InstanceType(value="t3.medium"),
        "provider_name": "aws-us-east-1",
        "provider_type": "aws",
        "template_id": "tmpl-001",
        "image_id": "ami-abc123",
    }
    defaults.update(overrides)
    return Machine(**defaults)


@pytest.mark.unit
@pytest.mark.application
class TestMachineDTOFromDomain:
    # ------------------------------------------------------------------
    # Basic field population
    # ------------------------------------------------------------------

    def test_from_domain_populates_public_dns_name(self):
        machine = _make_machine(public_dns_name="ec2-1-2-3-4.compute.amazonaws.com")
        dto = MachineDTO.from_domain(machine)
        assert dto.public_dns_name == "ec2-1-2-3-4.compute.amazonaws.com"

    def test_from_domain_public_dns_name_none_when_not_set(self):
        machine = _make_machine()
        dto = MachineDTO.from_domain(machine)
        assert dto.public_dns_name is None

    def test_from_domain_populates_provider_name(self):
        machine = _make_machine(provider_name="aws-us-east-1")
        dto = MachineDTO.from_domain(machine)
        assert dto.provider_name == "aws-us-east-1"

    def test_from_domain_private_dns_name_populated(self):
        machine = _make_machine(private_dns_name="ip-10-0-0-1.ec2.internal")
        dto = MachineDTO.from_domain(machine)
        assert dto.private_dns_name == "ip-10-0-0-1.ec2.internal"

    # ------------------------------------------------------------------
    # All fields always populated — no long gate
    # ------------------------------------------------------------------

    def test_from_domain_populates_cloud_host_id(self):
        """cloud_host_id must be populated without any long flag."""
        machine = _make_machine(provider_data={"cloud_host_id": "aws-host-abc"})
        dto = MachineDTO.from_domain(machine)
        assert dto.cloud_host_id == "aws-host-abc"

    def test_from_domain_cloud_host_id_none_when_not_in_provider_data(self):
        machine = _make_machine(provider_data={})
        dto = MachineDTO.from_domain(machine)
        assert dto.cloud_host_id is None

    def test_from_domain_populates_provider_api(self):
        machine = _make_machine(provider_api="RunInstances")
        dto = MachineDTO.from_domain(machine)
        assert dto.provider_api == "RunInstances"

    def test_from_domain_populates_resource_id(self):
        machine = _make_machine(resource_id="r-abc123")
        dto = MachineDTO.from_domain(machine)
        assert dto.resource_id == "r-abc123"

    def test_from_domain_populates_price_type(self):
        machine = _make_machine(price_type="spot")
        dto = MachineDTO.from_domain(machine)
        assert dto.price_type == "spot"

    def test_from_domain_populates_health_checks(self):
        machine = _make_machine(provider_data={"health_checks": {"status": "ok"}})
        dto = MachineDTO.from_domain(machine)
        assert dto.health_checks == {"status": "ok"}

    def test_from_domain_populates_metadata(self):
        machine = _make_machine(metadata={"key": "value"})
        dto = MachineDTO.from_domain(machine)
        assert dto.metadata == {"key": "value"}

    def test_from_domain_populates_return_request_id(self):
        machine = _make_machine(return_request_id="ret-001")
        dto = MachineDTO.from_domain(machine)
        assert dto.return_request_id == "ret-001"

    def test_from_domain_populates_provider_data(self):
        machine = _make_machine(provider_data={"foo": "bar"})
        dto = MachineDTO.from_domain(machine)
        assert dto.provider_data == {"foo": "bar"}

    def test_from_domain_populates_version(self):
        machine = _make_machine(version=7)
        dto = MachineDTO.from_domain(machine)
        assert dto.version == 7

    # ------------------------------------------------------------------
    # timestamp_format parameter
    # ------------------------------------------------------------------

    def test_from_domain_launch_time_auto_emits_unix_int(self):
        ts = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        machine = _make_machine(launch_time=ts)
        dto = MachineDTO.from_domain(machine)
        assert dto.launch_time == int(ts.timestamp())

    def test_from_domain_launch_time_iso_emits_string(self):
        ts = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        machine = _make_machine(launch_time=ts)
        dto = MachineDTO.from_domain(machine, timestamp_format="iso")
        assert isinstance(dto.launch_time, str)
        assert "2024-01-15" in dto.launch_time

    def test_from_domain_launch_time_none_when_not_set(self):
        machine = _make_machine()
        dto = MachineDTO.from_domain(machine)
        assert dto.launch_time is None

    # ------------------------------------------------------------------
    # Round-trip and field declaration guards
    # ------------------------------------------------------------------

    def test_cloud_host_id_roundtrip(self):
        """cloud_host_id must survive model_dump/model_validate round-trip."""
        machine = _make_machine(provider_data={"cloud_host_id": "aws-host-xyz"})
        dto = MachineDTO.from_domain(machine)
        restored = MachineDTO.model_validate(dto.model_dump())
        assert restored.cloud_host_id == "aws-host-xyz"

    def test_cloud_host_id_declared_on_model(self):
        """Regression guard: field must be declared so Pydantic does not silently drop it."""
        assert "cloud_host_id" in MachineDTO.model_fields

    def test_all_previously_gated_fields_declared(self):
        """Regression guard: all formerly long-gated fields must be declared on the model."""
        for field in (
            "provider_api",
            "resource_id",
            "price_type",
            "cloud_host_id",
            "metadata",
            "health_checks",
            "provider_data",
            "version",
            "return_request_id",
        ):
            assert field in MachineDTO.model_fields, f"Missing field: {field}"
