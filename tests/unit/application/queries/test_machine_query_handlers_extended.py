"""Unit tests for application/queries/machine_query_handlers.py — extended coverage."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.dto.queries import GetMachineQuery, ListMachinesQuery
from orb.application.queries.machine_query_handlers import GetMachineHandler, ListMachinesHandler
from orb.domain.base.exceptions import EntityNotFoundError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_machine(
    machine_id="m-001",
    request_id="req-001",
    provider_name="aws",
    provider_type="aws",
    name="my-machine",
    status_val="running",
    instance_type="t3.small",
    private_ip=None,
    public_ip=None,
):
    m = MagicMock()
    m.machine_id = machine_id
    m.request_id = request_id
    m.provider_name = provider_name
    m.provider_type = provider_type
    m.name = name
    m.instance_type = instance_type
    m.private_ip = private_ip
    m.public_ip = public_ip
    status = MagicMock()
    status.value = status_val
    m.status = status
    return m


def _make_uow_factory(
    machine_get=None,
    machines_find_active=None,
    machines_find_by_status=None,
    machines_find_by_request=None,
    machines_get_all=None,
    request_get=None,
):
    uow = MagicMock()
    uow.machines.get_by_id.return_value = machine_get
    uow.machines.find_active_machines.return_value = machines_find_active or []
    uow.machines.find_by_status.return_value = machines_find_by_status or []
    uow.machines.find_by_request_id.return_value = machines_find_by_request or []
    uow.machines.get_all.return_value = machines_get_all or []
    uow.requests.get_by_id.return_value = request_get

    @contextmanager
    def _create():
        yield uow

    factory = MagicMock()
    factory.create_unit_of_work.side_effect = _create
    return factory


def _make_machine_dto(machine):
    dto = MagicMock()
    dto.machine_id = machine.machine_id
    return dto


def _make_logger():
    return MagicMock()


def _make_error_handler():
    return MagicMock()


# ---------------------------------------------------------------------------
# GetMachineHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMachineHandler:
    def _handler(self, machine=None):
        uow_factory = _make_uow_factory(machine_get=machine)
        return GetMachineHandler(
            uow_factory=uow_factory,
            logger=_make_logger(),
            error_handler=_make_error_handler(),
        )

    @pytest.mark.asyncio
    async def test_returns_machine_dto_on_success(self):
        machine = _make_machine()
        h = self._handler(machine=machine)

        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            return_value=MagicMock(machine_id="m-001"),
        ):
            query = GetMachineQuery(machine_id="m-001")
            result = await h.execute_query(query)
            assert result.machine_id == "m-001"

    @pytest.mark.asyncio
    async def test_raises_entity_not_found_when_missing(self):
        h = self._handler(machine=None)
        query = GetMachineQuery(machine_id="m-missing")
        with pytest.raises(EntityNotFoundError):
            with patch(
                "orb.application.dto.responses.MachineDTO.from_domain", return_value=MagicMock()
            ):
                await h.execute_query(query)

    @pytest.mark.asyncio
    async def test_logs_error_on_not_found(self):
        logger = _make_logger()
        uow_factory = _make_uow_factory(machine_get=None)
        h = GetMachineHandler(
            uow_factory=uow_factory, logger=logger, error_handler=_make_error_handler()
        )
        with pytest.raises(EntityNotFoundError):
            with patch(
                "orb.application.dto.responses.MachineDTO.from_domain", return_value=MagicMock()
            ):
                await h.execute_query(GetMachineQuery(machine_id="m-x"))
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_generic_exception_re_raised(self):
        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.side_effect = RuntimeError("db gone")
        h = GetMachineHandler(
            uow_factory=uow_factory, logger=_make_logger(), error_handler=_make_error_handler()
        )
        with pytest.raises(RuntimeError, match="db gone"):
            await h.execute_query(GetMachineQuery(machine_id="m-1"))


# ---------------------------------------------------------------------------
# ListMachinesHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListMachinesHandler:
    def _handler(
        self,
        machines_find_active=None,
        machines_find_by_status=None,
        machines_find_by_request=None,
        machines_get_all=None,
        request_get=None,
    ):
        uow_factory = _make_uow_factory(
            machines_find_active=machines_find_active,
            machines_find_by_status=machines_find_by_status,
            machines_find_by_request=machines_find_by_request,
            machines_get_all=machines_get_all,
            request_get=request_get,
        )
        sync_svc = MagicMock()
        sync_svc.fetch_provider_machines = AsyncMock(return_value=([], {}))
        sync_svc.sync_machines_with_provider = AsyncMock(return_value=([], []))
        filter_svc = MagicMock()
        filter_svc.apply_filters.side_effect = lambda items, _: items
        return ListMachinesHandler(
            uow_factory=uow_factory,
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            container=MagicMock(),
            command_bus=MagicMock(),
            generic_filter_service=filter_svc,
            machine_sync_service=sync_svc,
        )

    @pytest.mark.asyncio
    async def test_all_resources_uses_find_active(self):
        machines = [_make_machine("m-1"), _make_machine("m-2")]
        h = self._handler(machines_find_active=machines)
        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            side_effect=lambda m, **kw: SimpleNamespace(machine_id=m.machine_id),
        ):
            q = ListMachinesQuery(all_resources=True, limit=None)
            result = await h.execute_query(q)
        assert result.total_count == 2

    @pytest.mark.asyncio
    async def test_filter_by_status(self):
        machines = [_make_machine("m-1", status_val="running")]
        h = self._handler(machines_find_by_status=machines)
        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            side_effect=lambda m, **kw: SimpleNamespace(machine_id=m.machine_id),
        ):
            q = ListMachinesQuery(status="running", limit=None)
            result = await h.execute_query(q)
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_filter_by_request_id(self):
        machines = [_make_machine("m-1", request_id="req-42")]
        h = self._handler(machines_find_by_request=machines)
        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            side_effect=lambda m, **kw: SimpleNamespace(machine_id=m.machine_id),
        ):
            q = ListMachinesQuery(request_id="req-42", limit=None)
            result = await h.execute_query(q)
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_default_uses_get_all(self):
        machines = [_make_machine()]
        h = self._handler(machines_get_all=machines)
        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            side_effect=lambda m, **kw: SimpleNamespace(machine_id=m.machine_id),
        ):
            q = ListMachinesQuery(limit=None)
            result = await h.execute_query(q)
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_provider_name_filter(self):
        machines = [
            _make_machine("m-1", provider_name="aws-us-east-1"),
            _make_machine("m-2", provider_name="azure"),
        ]
        h = self._handler(machines_find_active=machines)
        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            side_effect=lambda m, **kw: SimpleNamespace(machine_id=m.machine_id),
        ):
            q = ListMachinesQuery(all_resources=True, provider_name="aws", limit=None)
            result = await h.execute_query(q)
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_provider_type_filter(self):
        machines = [
            _make_machine("m-1", provider_type="aws"),
            _make_machine("m-2", provider_type="k8s"),
        ]
        h = self._handler(machines_find_active=machines)
        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            side_effect=lambda m, **kw: SimpleNamespace(machine_id=m.machine_id),
        ):
            q = ListMachinesQuery(all_resources=True, provider_type="k8s", limit=None)
            result = await h.execute_query(q)
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_q_filter_by_machine_id(self):
        machines = [
            _make_machine("m-abc-001", name="alpha"),
            _make_machine("m-xyz-999", name="beta"),
        ]
        h = self._handler(machines_find_active=machines)
        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            side_effect=lambda m, **kw: SimpleNamespace(machine_id=m.machine_id),
        ):
            q = ListMachinesQuery(all_resources=True, q="abc", limit=None)
            result = await h.execute_query(q)
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_sort_ascending(self):
        machines = [
            _make_machine("m-b", name="beta"),
            _make_machine("m-a", name="alpha"),
        ]
        h = self._handler(machines_find_active=machines)
        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            side_effect=lambda m, **kw: SimpleNamespace(machine_id=m.machine_id, name=m.name),
        ):
            q = ListMachinesQuery(all_resources=True, sort="+name", limit=None)
            result = await h.execute_query(q)
        names = [item.name for item in result.items]
        assert names == ["alpha", "beta"]

    @pytest.mark.asyncio
    async def test_sort_descending(self):
        machines = [
            _make_machine("m-a", name="alpha"),
            _make_machine("m-b", name="beta"),
        ]
        h = self._handler(machines_find_active=machines)
        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            side_effect=lambda m, **kw: SimpleNamespace(machine_id=m.machine_id, name=m.name),
        ):
            q = ListMachinesQuery(all_resources=True, sort="-name", limit=None)
            result = await h.execute_query(q)
        names = [item.name for item in result.items]
        assert names == ["beta", "alpha"]

    @pytest.mark.asyncio
    async def test_limit_and_offset_applied(self):
        machines = [_make_machine(f"m-{i}") for i in range(10)]
        h = self._handler(machines_find_active=machines)
        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            side_effect=lambda m, **kw: SimpleNamespace(machine_id=m.machine_id),
        ):
            q = ListMachinesQuery(all_resources=True, limit=3, offset=2)
            result = await h.execute_query(q)
        assert len(result.items) == 3
        assert result.total_count == 10

    @pytest.mark.asyncio
    async def test_limit_clamped_to_1000(self):
        machines = [_make_machine(f"m-{i}") for i in range(5)]
        h = self._handler(machines_find_active=machines)
        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            side_effect=lambda m, **kw: SimpleNamespace(machine_id=m.machine_id),
        ):
            q = ListMachinesQuery(all_resources=True, limit=9999)
            result = await h.execute_query(q)
        assert len(result.items) == 5  # only 5 machines, all returned

    @pytest.mark.asyncio
    async def test_zero_limit_returns_empty_items(self):
        machines = [_make_machine()]
        h = self._handler(machines_find_active=machines)
        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            side_effect=lambda m, **kw: SimpleNamespace(machine_id=m.machine_id),
        ):
            q = ListMachinesQuery(all_resources=True, limit=0)
            result = await h.execute_query(q)
        assert result.items == []
        assert result.total_count == 1  # total is pre-slice

    @pytest.mark.asyncio
    async def test_none_limit_no_cap(self):
        machines = [_make_machine(f"m-{i}") for i in range(5)]
        h = self._handler(machines_find_active=machines)
        with patch(
            "orb.application.dto.responses.MachineDTO.from_domain",
            side_effect=lambda m, **kw: SimpleNamespace(machine_id=m.machine_id),
        ):
            q = ListMachinesQuery(all_resources=True, limit=None)
            result = await h.execute_query(q)
        assert len(result.items) == 5

    @pytest.mark.asyncio
    async def test_exception_is_re_raised(self):
        uow_factory = MagicMock()
        uow_factory.create_unit_of_work.side_effect = RuntimeError("db gone")
        sync_svc = MagicMock()
        h = ListMachinesHandler(
            uow_factory=uow_factory,
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            container=MagicMock(),
            command_bus=MagicMock(),
            generic_filter_service=MagicMock(),
            machine_sync_service=sync_svc,
        )
        with pytest.raises(RuntimeError, match="db gone"):
            await h.execute_query(ListMachinesQuery())
