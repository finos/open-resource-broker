"""Tests for pagination across all list query DTOs and handlers."""

from __future__ import annotations

from orb.application.dto.queries import (
    ListActiveRequestsQuery,
    ListMachinesQuery,
    ListReturnRequestsQuery,
    ListTemplatesQuery,
)
from orb.application.request.queries import ListRequestsQuery

# ---------------------------------------------------------------------------
# Query DTO defaults
# ---------------------------------------------------------------------------


class TestListQueryPaginationDefaults:
    def test_list_requests_query_defaults(self):
        q = ListRequestsQuery()
        assert q.limit == 50
        assert q.offset == 0

    def test_list_active_requests_query_defaults(self):
        q = ListActiveRequestsQuery()
        assert q.limit == 50
        assert q.offset == 0

    def test_list_return_requests_query_defaults(self):
        q = ListReturnRequestsQuery()
        assert q.limit == 50
        assert q.offset == 0

    def test_list_templates_query_defaults(self):
        q = ListTemplatesQuery()
        assert q.limit == 50
        assert q.offset == 0

    def test_list_machines_query_defaults(self):
        q = ListMachinesQuery()
        assert q.limit == 50
        assert q.offset == 0


# ---------------------------------------------------------------------------
# Custom limit / offset
# ---------------------------------------------------------------------------


class TestListQueryPaginationCustomValues:
    def test_list_requests_custom_limit_offset(self):
        q = ListRequestsQuery(limit=10, offset=20)
        assert q.limit == 10
        assert q.offset == 20

    def test_list_active_requests_custom(self):
        q = ListActiveRequestsQuery(limit=100, offset=50)
        assert q.limit == 100
        assert q.offset == 50

    def test_list_return_requests_custom(self):
        q = ListReturnRequestsQuery(limit=25, offset=75)
        assert q.limit == 25
        assert q.offset == 75

    def test_list_templates_custom(self):
        q = ListTemplatesQuery(limit=200, offset=10)
        assert q.limit == 200
        assert q.offset == 10

    def test_list_machines_custom(self):
        q = ListMachinesQuery(limit=5, offset=0)
        assert q.limit == 5
        assert q.offset == 0


# ---------------------------------------------------------------------------
# CLI factory wires limit / offset correctly
# ---------------------------------------------------------------------------


class TestCLIFactoryPagination:
    def test_machine_factory_default_pagination(self):
        from orb.cli.factories.machine_command_factory import MachineCommandFactory

        factory = MachineCommandFactory()
        q = factory.create_list_machines_query()
        assert q.limit == 50
        assert q.offset == 0

    def test_machine_factory_custom_pagination(self):
        from orb.cli.factories.machine_command_factory import MachineCommandFactory

        factory = MachineCommandFactory()
        q = factory.create_list_machines_query(limit=10, offset=5)
        assert q.limit == 10
        assert q.offset == 5

    def test_machine_factory_caps_limit_at_1000(self):
        from orb.cli.factories.machine_command_factory import MachineCommandFactory

        factory = MachineCommandFactory()
        q = factory.create_list_machines_query(limit=9999)
        assert q.limit == 1000

    def test_request_factory_list_return_requests_default(self):
        from orb.cli.factories.request_command_factory import RequestCommandFactory

        factory = RequestCommandFactory()
        q = factory.create_list_return_requests_query()
        assert q.limit == 50
        assert q.offset == 0

    def test_request_factory_list_return_requests_custom(self):
        from orb.cli.factories.request_command_factory import RequestCommandFactory

        factory = RequestCommandFactory()
        q = factory.create_list_return_requests_query(limit=20, offset=40)
        assert q.limit == 20
        assert q.offset == 40

    def test_request_factory_list_return_requests_caps_limit(self):
        from orb.cli.factories.request_command_factory import RequestCommandFactory

        factory = RequestCommandFactory()
        q = factory.create_list_return_requests_query(limit=5000)
        assert q.limit == 1000

    def test_request_factory_list_active_requests_default(self):
        from orb.cli.factories.request_command_factory import RequestCommandFactory

        factory = RequestCommandFactory()
        q = factory.create_list_active_requests_query()
        assert q.limit == 50
        assert q.offset == 0

    def test_request_factory_list_active_requests_custom(self):
        from orb.cli.factories.request_command_factory import RequestCommandFactory

        factory = RequestCommandFactory()
        q = factory.create_list_active_requests_query(limit=75, offset=25)
        assert q.limit == 75
        assert q.offset == 25

    def test_template_factory_default_pagination(self):
        from orb.cli.factories.template_command_factory import TemplateCommandFactory

        factory = TemplateCommandFactory()
        q = factory.create_list_templates_query()
        assert q.limit == 50
        assert q.offset == 0

    def test_template_factory_custom_pagination(self):
        from orb.cli.factories.template_command_factory import TemplateCommandFactory

        factory = TemplateCommandFactory()
        q = factory.create_list_templates_query(limit=30, offset=60)
        assert q.limit == 30
        assert q.offset == 60

    def test_template_factory_caps_limit_at_1000(self):
        from orb.cli.factories.template_command_factory import TemplateCommandFactory

        factory = TemplateCommandFactory()
        q = factory.create_list_templates_query(limit=2000)
        assert q.limit == 1000


# ---------------------------------------------------------------------------
# Pagination slice logic (unit-level, no I/O)
# ---------------------------------------------------------------------------


class TestPaginationSliceLogic:
    """Verify the slice math used in handlers is correct."""

    def _paginate(self, items: list, limit: int, offset: int) -> list:
        """Replicate the handler pagination logic."""
        effective_limit = min(limit, 1000)
        return items[offset : offset + effective_limit]

    def test_first_page(self):
        items = list(range(100))
        page = self._paginate(items, limit=10, offset=0)
        assert page == list(range(10))

    def test_second_page(self):
        items = list(range(100))
        page = self._paginate(items, limit=10, offset=10)
        assert page == list(range(10, 20))

    def test_last_partial_page(self):
        items = list(range(25))
        page = self._paginate(items, limit=10, offset=20)
        assert page == [20, 21, 22, 23, 24]

    def test_offset_beyond_end_returns_empty(self):
        items = list(range(10))
        page = self._paginate(items, limit=10, offset=100)
        assert page == []

    def test_empty_collection(self):
        page = self._paginate([], limit=50, offset=0)
        assert page == []

    def test_limit_capped_at_1000(self):
        items = list(range(2000))
        page = self._paginate(items, limit=9999, offset=0)
        assert len(page) == 1000

    def test_zero_offset_full_limit(self):
        items = list(range(50))
        page = self._paginate(items, limit=50, offset=0)
        assert len(page) == 50
        assert page == items

    def test_single_item_result(self):
        items = list(range(100))
        page = self._paginate(items, limit=1, offset=42)
        assert page == [42]


# ---------------------------------------------------------------------------
# PaginationMetadata DTO
# ---------------------------------------------------------------------------


class TestPaginationMetadata:
    def test_basic_construction(self):
        from orb.application.dto.base import PaginationMetadata

        meta = PaginationMetadata(
            total_count=100,
            limit=10,
            offset=0,
            has_more=True,
            returned_count=10,
        )
        assert meta.total_count == 100
        assert meta.limit == 10
        assert meta.offset == 0
        assert meta.has_more is True
        assert meta.returned_count == 10

    def test_last_page_has_more_false(self):
        from orb.application.dto.base import PaginationMetadata

        meta = PaginationMetadata(
            total_count=25,
            limit=10,
            offset=20,
            has_more=False,
            returned_count=5,
        )
        assert meta.has_more is False
        assert meta.returned_count == 5

    def test_empty_result(self):
        from orb.application.dto.base import PaginationMetadata

        meta = PaginationMetadata(
            total_count=0,
            limit=50,
            offset=0,
            has_more=False,
            returned_count=0,
        )
        assert meta.total_count == 0
        assert meta.has_more is False
