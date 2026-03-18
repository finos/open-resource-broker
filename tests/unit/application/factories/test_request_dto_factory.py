"""Unit tests for RequestDTOFactory.map_machine_status_to_result."""

import pytest

from orb.application.factories.request_dto_factory import RequestDTOFactory
from orb.domain.request.request_types import RequestType


@pytest.fixture
def factory():
    return RequestDTOFactory()


class TestMapMachineStatusToResultReturnRequest:
    def test_shutting_down_is_executing(self, factory):
        assert factory.map_machine_status_to_result("shutting-down", RequestType.RETURN) == "executing"

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
