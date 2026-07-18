"""Unit tests for orb.cli.factories.machine_command_factory.MachineCommandFactory.

Verifies that each factory method produces the correct CQRS query/command
with the right field values. No buses, no DI container — pure data construction.
"""

from __future__ import annotations

import pytest

from orb.cli.factories.machine_command_factory import MachineCommandFactory


@pytest.fixture
def factory() -> MachineCommandFactory:
    return MachineCommandFactory()


@pytest.mark.unit
class TestCreateListMachinesQuery:
    def test_defaults(self, factory):
        q = factory.create_list_machines_query()
        assert q.limit == 50
        assert q.offset == 0
        assert q.status is None
        assert q.request_id is None

    def test_status_passed_through(self, factory):
        q = factory.create_list_machines_query(status="running")
        assert q.status == "running"

    def test_request_id_passed_through(self, factory):
        q = factory.create_list_machines_query(request_id="req-1")
        assert q.request_id == "req-1"

    def test_limit_capped_at_1000(self, factory):
        q = factory.create_list_machines_query(limit=9999)
        assert q.limit == 1000

    def test_offset_set(self, factory):
        q = factory.create_list_machines_query(offset=20)
        assert q.offset == 20

    def test_provider_name_from_kwargs(self, factory):
        q = factory.create_list_machines_query(provider_name="aws-prod")
        assert q.provider_name == "aws-prod"

    def test_provider_type_from_kwargs(self, factory):
        q = factory.create_list_machines_query(provider_type="k8s")
        assert q.provider_type == "k8s"


@pytest.mark.unit
class TestCreateGetMachineQuery:
    def test_machine_id_set(self, factory):
        q = factory.create_get_machine_query(machine_id="m-001")
        assert q.machine_id == "m-001"

    def test_extra_kwargs_ignored(self, factory):
        q = factory.create_get_machine_query(machine_id="m-002", unknown="x")
        assert q.machine_id == "m-002"


@pytest.mark.unit
class TestCreateUpdateMachineStatusCommand:
    def test_machine_id_and_status_set(self, factory):
        cmd = factory.create_update_machine_status_command(machine_id="m-1", status="stopped")
        assert cmd.machine_id == "m-1"
        assert cmd.status == "stopped"


@pytest.mark.unit
class TestCreateGetMultipleMachinesQuery:
    def test_machine_ids_set(self, factory):
        q = factory.create_get_multiple_machines_query(machine_ids=["m-1", "m-2"])
        assert set(q.machine_ids) == {"m-1", "m-2"}

    def test_provider_name_optional(self, factory):
        q = factory.create_get_multiple_machines_query(
            machine_ids=["m-1"], provider_name="aws-test"
        )
        assert q.provider_name == "aws-test"

    def test_include_requests_default_true(self, factory):
        q = factory.create_get_multiple_machines_query(machine_ids=["m-1"])
        assert q.include_requests is True

    def test_include_requests_set_false(self, factory):
        q = factory.create_get_multiple_machines_query(machine_ids=["m-1"], include_requests=False)
        assert q.include_requests is False
