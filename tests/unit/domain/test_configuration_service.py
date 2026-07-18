"""Unit tests for DomainConfigurationService."""

from unittest.mock import MagicMock

import pytest

from orb.domain.base.configuration_service import DomainConfigurationService
from orb.domain.constants import (
    REQUEST_ID_PATTERN,
    REQUEST_ID_PREFIX_ACQUIRE,
    REQUEST_ID_PREFIX_RETURN,
)


def _make_service(naming_config: dict) -> DomainConfigurationService:
    config_port = MagicMock()
    config_port.get_naming_config.return_value = naming_config
    return DomainConfigurationService(config_port)


@pytest.mark.unit
class TestDomainConfigurationService:
    def test_get_acquire_request_prefix_from_config(self):
        svc = _make_service({"prefixes": {"request": "rq-"}})
        assert svc.get_acquire_request_prefix() == "rq-"

    def test_get_acquire_request_prefix_falls_back_to_default(self):
        svc = _make_service({})
        assert svc.get_acquire_request_prefix() == REQUEST_ID_PREFIX_ACQUIRE

    def test_get_return_request_prefix_from_config(self):
        svc = _make_service({"prefixes": {"return": "rt-"}})
        assert svc.get_return_request_prefix() == "rt-"

    def test_get_return_request_prefix_falls_back_to_default(self):
        svc = _make_service({})
        assert svc.get_return_request_prefix() == REQUEST_ID_PREFIX_RETURN

    def test_get_request_id_pattern_from_config(self):
        custom = r"^[a-z]+$"
        svc = _make_service({"patterns": {"request_id": custom}})
        assert svc.get_request_id_pattern() == custom

    def test_get_request_id_pattern_falls_back_to_default(self):
        svc = _make_service({})
        assert svc.get_request_id_pattern() == REQUEST_ID_PATTERN
