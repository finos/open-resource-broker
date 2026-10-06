"""Tests for warn_if_dashboard_unauthenticated in orb.ui.api_http."""

from __future__ import annotations

from unittest.mock import MagicMock


def _warn(server_config, logger) -> None:
    from orb.ui.api_http import warn_if_dashboard_unauthenticated

    warn_if_dashboard_unauthenticated(server_config, logger)


class TestWarnIfDashboardUnauthenticated:
    def test_warns_when_auth_enabled(self):
        server_config = MagicMock()
        server_config.auth.enabled = True
        logger = MagicMock()

        _warn(server_config, logger)

        logger.warning.assert_called_once()
        message = logger.warning.call_args.args[0]
        assert "dashboard" in message.lower()

    def test_no_warning_when_auth_disabled(self):
        server_config = MagicMock()
        server_config.auth.enabled = False
        logger = MagicMock()

        _warn(server_config, logger)

        logger.warning.assert_not_called()

    def test_no_warning_when_auth_attribute_missing(self):
        """A server_config stub with no ``auth`` attribute must not raise or warn."""
        logger = MagicMock()

        _warn(object(), logger)

        logger.warning.assert_not_called()
