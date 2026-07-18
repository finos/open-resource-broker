"""Unit tests for orb.cli.factories.request_command_factory.RequestCommandFactory.

Verifies that each factory method produces the correct CQRS query/command
with the right field values.
"""

from __future__ import annotations

import pytest

from orb.cli.factories.request_command_factory import RequestCommandFactory


@pytest.fixture
def factory() -> RequestCommandFactory:
    return RequestCommandFactory()


@pytest.mark.unit
class TestCreateCreateRequestCommand:
    def test_template_id_and_count(self, factory):
        cmd = factory.create_create_request_command(template_id="tmpl-1", count=5)
        assert cmd.template_id == "tmpl-1"
        assert cmd.requested_count == 5


@pytest.mark.unit
class TestCreateGetRequestStatusQuery:
    def test_request_id_set(self, factory):
        q = factory.create_get_request_status_query(request_id="req-1")
        assert q.request_id == "req-1"

    def test_provider_name_from_kwargs(self, factory):
        q = factory.create_get_request_status_query(request_id="req-1", provider_name="aws-dev")
        assert q.provider_name == "aws-dev"

    def test_provider_positional_fallback(self, factory):
        q = factory.create_get_request_status_query(request_id="req-1", provider="aws-fb")
        assert q.provider_name == "aws-fb"

    def test_lightweight_default_false(self, factory):
        q = factory.create_get_request_status_query(request_id="req-1")
        assert q.lightweight is False

    def test_lightweight_set_true(self, factory):
        q = factory.create_get_request_status_query(request_id="req-1", lightweight=True)
        assert q.lightweight is True


@pytest.mark.unit
class TestCreateListRequestsQuery:
    def test_defaults(self, factory):
        q = factory.create_list_requests_query()
        assert q.limit == 50
        assert q.status is None

    def test_status_passed_through(self, factory):
        q = factory.create_list_requests_query(status="active")
        assert q.status == "active"

    def test_limit_none_defaults_to_50(self, factory):
        q = factory.create_list_requests_query(limit=None)
        assert q.limit == 50

    def test_limit_set(self, factory):
        q = factory.create_list_requests_query(limit=10)
        assert q.limit == 10

    def test_provider_name_from_kwargs(self, factory):
        q = factory.create_list_requests_query(provider_name="aws-prd")
        assert q.provider_name == "aws-prd"


@pytest.mark.unit
class TestCreateCancelRequestCommand:
    def test_request_id_set(self, factory):
        cmd = factory.create_cancel_request_command(request_id="req-cancel")
        assert cmd.request_id == "req-cancel"

    def test_default_reason(self, factory):
        cmd = factory.create_cancel_request_command(request_id="req-1")
        assert "Cancelled" in cmd.reason or "user" in cmd.reason.lower()

    def test_custom_reason_from_kwargs(self, factory):
        cmd = factory.create_cancel_request_command(request_id="req-1", reason="Test cancel")
        assert cmd.reason == "Test cancel"


@pytest.mark.unit
class TestCreateReturnRequestCommand:
    def test_machine_ids_set(self, factory):
        cmd = factory.create_return_request_command(machine_ids=["m-1", "m-2"])
        assert set(cmd.machine_ids) == {"m-1", "m-2"}

    def test_empty_machine_ids(self, factory):
        cmd = factory.create_return_request_command(machine_ids=[])
        assert cmd.machine_ids == []


@pytest.mark.unit
class TestCreateListReturnRequestsQuery:
    def test_defaults(self, factory):
        q = factory.create_list_return_requests_query()
        assert q.limit == 50
        assert q.offset == 0

    def test_status_passed_through(self, factory):
        q = factory.create_list_return_requests_query(status="pending")
        assert q.status == "pending"

    def test_limit_capped_at_1000(self, factory):
        q = factory.create_list_return_requests_query(limit=9999)
        assert q.limit == 1000


@pytest.mark.unit
class TestCreateListActiveRequestsQuery:
    def test_defaults(self, factory):
        q = factory.create_list_active_requests_query()
        assert q.limit == 50
        assert q.offset == 0

    def test_provider_name_passed_through(self, factory):
        q = factory.create_list_active_requests_query(provider_name="aws-dev")
        assert q.provider_name == "aws-dev"

    def test_limit_capped_at_1000(self, factory):
        q = factory.create_list_active_requests_query(limit=99999)
        assert q.limit == 1000


@pytest.mark.unit
class TestCreateGetMultipleRequestsQuery:
    def test_request_ids_set(self, factory):
        q = factory.create_get_multiple_requests_query(request_ids=["r-1", "r-2"])
        assert set(q.request_ids) == {"r-1", "r-2"}

    def test_provider_name_optional(self, factory):
        q = factory.create_get_multiple_requests_query(request_ids=["r-1"], provider_name="aws")
        assert q.provider_name == "aws"

    def test_lightweight_default_false(self, factory):
        q = factory.create_get_multiple_requests_query(request_ids=["r-1"])
        assert q.lightweight is False

    def test_include_machines_default_true(self, factory):
        q = factory.create_get_multiple_requests_query(request_ids=["r-1"])
        assert q.include_machines is True
