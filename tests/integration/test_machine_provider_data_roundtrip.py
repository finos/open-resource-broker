"""Integration tests covering the end-to-end provider_data path.

Regression coverage for two related bugs that watch CLI surfaced:

  1. RequestStatusManagementService._create_machine_aggregate built a Machine
     without copying provider_data from the input dict, so freshly-provisioned
     machines persisted with provider_data={} and watch showed AZ "unknown".
  2. MachineSyncService._create_machine_from_processed_data had the same drop
     in its sync path.

These tests build Machine aggregates via the *real* factory methods, persist
through MachineRepositoryImpl + JSONStorageStrategy on a tmp_path JSON file,
read back, and assert provider_data keys (availability_zone, vcpus, region)
survive the round-trip.

Unit-level tests on the watch orchestrator can pass while real storage drops
fields. This integration test fails if either factory regresses.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.services.machine_sync_service import MachineSyncService
from orb.application.services.request_status_management_service import (
    RequestStatusManagementService,
)
from orb.infrastructure.storage.json.strategy import JSONStorageStrategy
from orb.infrastructure.storage.repositories.machine_repository import (
    MachineRepositoryImpl,
)


@pytest.fixture
def machine_repository(tmp_path):
    """Real MachineRepositoryImpl backed by a JSONStorageStrategy on tmp_path."""
    storage = JSONStorageStrategy(
        file_path=str(tmp_path / "machines.json"),
        entity_type="machines",
        backup_enabled=False,
    )
    return MachineRepositoryImpl(storage)


@pytest.fixture
def fake_request():
    """Minimal Request stand-in: only the attributes the factories read."""
    request = MagicMock()
    request.request_id = "req-test-123"
    request.template_id = "tpl-test"
    request.provider_type = "aws"
    request.provider_name = "aws-test"
    request.provider_api = "RunInstances"
    return request


def _aws_instance_data(instance_id: str) -> dict:
    """Shape that AWSMachineAdapter.create_machine_from_aws_instance produces.

    The provider adapter populates provider_data with placement-derived fields
    (availability_zone, vcpus, region, cloud_host_id, etc.). Anything that
    drops this dict on the way to storage is the bug under test.
    """
    return {
        "instance_id": instance_id,
        "name": instance_id,
        "request_id": "req-test-123",
        "status": "pending",
        "instance_type": "t3.medium",
        "image_id": "ami-test",
        "private_ip": "10.0.0.1",
        "public_ip": None,
        "private_dns_name": "ip-10-0-0-1",
        "public_dns_name": None,
        "subnet_id": "subnet-test",
        "security_group_ids": ["sg-test"],
        "launch_time": "2026-05-13T10:00:00+00:00",
        "tags": {"Name": instance_id},
        "metadata": {"ami_id": "ami-test"},
        "provider_data": {
            "cloud_host_id": instance_id,
            "availability_zone": "eu-west-1a",
            "region": "eu-west-1",
            "vcpus": 2,
        },
        "resource_id": "rsv-test",
        "price_type": "ondemand",
    }


@pytest.mark.integration
class TestMachineProviderDataRoundTrip:
    """Round-trip provider_data through real factories and real JSON storage."""

    def test_create_machine_aggregate_persists_provider_data(
        self, machine_repository, fake_request
    ):
        """RequestStatusManagementService._create_machine_aggregate must
        forward provider_data so it survives storage round-trip."""
        service = RequestStatusManagementService(uow_factory=MagicMock(), logger=MagicMock())
        instance_data = _aws_instance_data("i-00000000aaaa1111")

        machine = service._create_machine_aggregate(
            instance_data, fake_request, fake_request.template_id
        )

        # In-memory check: the field is on the aggregate before persistence.
        assert machine.provider_data.get("availability_zone") == "eu-west-1a"
        assert machine.provider_data.get("vcpus") == 2
        assert machine.provider_data.get("region") == "eu-west-1"
        assert machine.provider_data.get("cloud_host_id") == "i-00000000aaaa1111"

        # Persist and reload through real storage strategy.
        machine_repository.save(machine)
        loaded = machine_repository.find_by_id("i-00000000aaaa1111")

        assert loaded is not None
        assert loaded.provider_data.get("availability_zone") == "eu-west-1a"
        assert loaded.provider_data.get("vcpus") == 2
        assert loaded.provider_data.get("region") == "eu-west-1"
        assert loaded.provider_data.get("cloud_host_id") == "i-00000000aaaa1111"

    def test_create_machine_from_processed_data_persists_provider_data(
        self, machine_repository, fake_request
    ):
        """MachineSyncService._create_machine_from_processed_data must
        forward provider_data so it survives storage round-trip."""
        service = MachineSyncService(
            command_bus=MagicMock(),
            uow_factory=MagicMock(),
            config_port=MagicMock(),
            logger=MagicMock(),
        )
        processed = _aws_instance_data("i-00000000bbbb2222")

        machine = service._create_machine_from_processed_data(processed, fake_request)

        assert machine.provider_data.get("availability_zone") == "eu-west-1a"
        assert machine.provider_data.get("vcpus") == 2

        machine_repository.save(machine)
        loaded = machine_repository.find_by_id("i-00000000bbbb2222")

        assert loaded is not None
        assert loaded.provider_data.get("availability_zone") == "eu-west-1a"
        assert loaded.provider_data.get("vcpus") == 2
        assert loaded.provider_data.get("region") == "eu-west-1"

    def test_empty_provider_data_input_does_not_crash(
        self, machine_repository, fake_request
    ):
        """Defensive: an instance dict with no provider_data key still
        produces a valid Machine (provider_data defaults to {})."""
        service = RequestStatusManagementService(uow_factory=MagicMock(), logger=MagicMock())
        instance_data = _aws_instance_data("i-00000000cccc3333")
        del instance_data["provider_data"]

        machine = service._create_machine_aggregate(
            instance_data, fake_request, fake_request.template_id
        )
        assert machine.provider_data == {}

        machine_repository.save(machine)
        loaded = machine_repository.find_by_id("i-00000000cccc3333")
        assert loaded is not None
        assert loaded.provider_data == {}
