"""Unit tests for API dependency functions."""

from unittest.mock import MagicMock

import pytest

from orb.api.dependencies import get_response_formatting_service
from orb.interface.response_formatting_service import ResponseFormattingService


@pytest.mark.unit
@pytest.mark.api
class TestGetResponseFormattingService:
    """Tests for get_response_formatting_service dependency."""

    def test_returns_singleton_from_di_container(self, monkeypatch):
        """get_response_formatting_service must return the DI-registered singleton,
        not construct a new instance on every call."""
        singleton = MagicMock(spec=ResponseFormattingService)
        mock_container = MagicMock()
        mock_container.get.return_value = singleton
        monkeypatch.setattr("orb.api.dependencies.get_di_container", lambda: mock_container)

        first = get_response_formatting_service()
        second = get_response_formatting_service()

        assert first is second, (
            "get_response_formatting_service should return the same instance on "
            "consecutive calls (DI singleton), but got two different objects"
        )
        mock_container.get.assert_called_with(ResponseFormattingService)
