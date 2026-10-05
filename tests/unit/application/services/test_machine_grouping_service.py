"""Unit tests for MachineGroupingService.

Covers:
- group_by_provider: grouping, missing machine, missing provider_api invariant.
- group_by_resource: grouping, capability-based direct-termination fallback,
  missing context skip, errors.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.services.machine_grouping_service import MachineGroupingService
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.operations import OperationType


def _make_uow_factory(machines_by_id: dict) -> MagicMock:
    repo = MagicMock()
    repo.get_by_id.side_effect = lambda mid: machines_by_id.get(mid)
    repo.find_by_id.side_effect = lambda mid: machines_by_id.get(mid)

    uow = MagicMock()
    uow.machines = repo
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)

    factory = MagicMock()
    factory.create_unit_of_work.return_value = uow
    return factory


def _make_machine(
    provider_type: str = "aws",
    provider_name: str = "aws-1",
    provider_api: str | None = "EC2Fleet",
):
    m = MagicMock()
    m.provider_type = provider_type
    m.provider_name = provider_name
    m.provider_api = provider_api
    return m


@pytest.mark.unit
class TestGroupByProvider:
    def test_groups_machines_by_type_name_api(self):
        m1 = _make_machine(provider_api="EC2Fleet")
        m2 = _make_machine(provider_api="ASG")
        m3 = _make_machine(provider_api="EC2Fleet")
        factory = _make_uow_factory({"m1": m1, "m2": m2, "m3": m3})
        service = MachineGroupingService(uow_factory=factory, logger=MagicMock())

        groups = service.group_by_provider(["m1", "m2", "m3"])

        assert groups[("aws", "aws-1", "EC2Fleet")] == ["m1", "m3"]
        assert groups[("aws", "aws-1", "ASG")] == ["m2"]

    def test_missing_machine_raises_entity_not_found(self):
        factory = _make_uow_factory({})
        service = MachineGroupingService(uow_factory=factory, logger=MagicMock())

        with pytest.raises(EntityNotFoundError):
            service.group_by_provider(["missing-1"])

    def test_missing_provider_api_raises_value_error(self):
        m1 = _make_machine(provider_api=None)
        factory = _make_uow_factory({"m1": m1})
        service = MachineGroupingService(uow_factory=factory, logger=MagicMock())

        with pytest.raises(ValueError, match="no provider_api"):
            service.group_by_provider(["m1"])

    def test_empty_machine_ids_returns_empty_dict(self):
        factory = _make_uow_factory({})
        service = MachineGroupingService(uow_factory=factory, logger=MagicMock())

        groups = service.group_by_provider([])

        assert groups == {}


def _make_resource_machine(
    provider_name: str = "aws-1",
    provider_api: str | None = "EC2Fleet",
    resource_id: str | None = "res-1",
    machine_id_value: str = "m-1",
):
    m = MagicMock()
    m.provider_name = provider_name
    m.provider_api = provider_api
    m.resource_id = resource_id
    m.machine_id.value = machine_id_value
    return m


def _make_provider_selection_port(*, supports_tag_instances: bool) -> MagicMock:
    """A ProviderSelectionPort stub whose capabilities report TAG_INSTANCES support."""
    port = MagicMock()
    capabilities = MagicMock()
    capabilities.supports_operation.side_effect = lambda op: (
        supports_tag_instances and op == OperationType.TAG_INSTANCES
    )
    port.get_strategy_capabilities.return_value = capabilities
    return port


@pytest.mark.unit
class TestGroupByResource:
    def test_groups_machines_with_full_context(self):
        m1 = _make_resource_machine(resource_id="res-1", machine_id_value="m-1")
        m2 = _make_resource_machine(resource_id="res-1", machine_id_value="m-2")
        factory = _make_uow_factory({"m-1": m1, "m-2": m2})
        service = MachineGroupingService(uow_factory=factory, logger=MagicMock())

        groups, skipped = service.group_by_resource(["m-1", "m-2"])

        key = ("aws-1", "EC2Fleet", "res-1")
        assert key in groups
        assert len(groups[key]) == 2
        assert skipped == []

    def test_direct_termination_fallback_when_provider_supports_tag_instances(self):
        m1 = _make_resource_machine(
            resource_id=None, provider_api=None, machine_id_value="i-abc123"
        )
        factory = _make_uow_factory({"i-abc123": m1})
        provider_selection_port = _make_provider_selection_port(supports_tag_instances=True)
        service = MachineGroupingService(
            uow_factory=factory,
            logger=MagicMock(),
            provider_selection_port=provider_selection_port,
        )

        groups, skipped = service.group_by_resource(["i-abc123"])

        assert skipped == []
        keys = list(groups.keys())
        assert len(keys) == 1
        _, provider_api, resource_id = keys[0]
        assert provider_api == "RunInstances"
        assert resource_id == "direct-i-abc123"

    def test_missing_resource_id_is_skipped_when_provider_lacks_tag_instances(self):
        m1 = _make_resource_machine(resource_id=None, machine_id_value="i-abc123")
        factory = _make_uow_factory({"i-abc123": m1})
        provider_selection_port = _make_provider_selection_port(supports_tag_instances=False)
        service = MachineGroupingService(
            uow_factory=factory,
            logger=MagicMock(),
            provider_selection_port=provider_selection_port,
        )

        groups, skipped = service.group_by_resource(["i-abc123"])

        assert groups == {}
        assert skipped == ["i-abc123"]

    def test_missing_resource_id_is_skipped_when_no_provider_selection_port(self):
        m1 = _make_resource_machine(resource_id=None, machine_id_value="node-xyz")
        factory = _make_uow_factory({"node-xyz": m1})
        service = MachineGroupingService(uow_factory=factory, logger=MagicMock())

        groups, skipped = service.group_by_resource(["node-xyz"])

        assert groups == {}
        assert skipped == ["node-xyz"]

    def test_missing_provider_api_with_resource_id_is_skipped(self):
        m1 = _make_resource_machine(resource_id="res-1", provider_api=None)
        factory = _make_uow_factory({"m-1": m1})
        service = MachineGroupingService(uow_factory=factory, logger=MagicMock())

        groups, skipped = service.group_by_resource(["m-1"])

        assert groups == {}
        assert skipped == ["m-1"]

    def test_machine_not_found_raises_value_error(self):
        factory = _make_uow_factory({})
        service = MachineGroupingService(uow_factory=factory, logger=MagicMock())

        with pytest.raises(ValueError, match="Cannot determine context"):
            service.group_by_resource(["missing-1"])
