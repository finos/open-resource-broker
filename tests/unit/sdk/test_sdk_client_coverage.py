"""Unit tests for sdk/client.py covering previously uncovered branches.

Targets:
  - ORBClient.initialize(): config_path missing raises ConfigurationError (line 170-173),
    scheduler-override pre-registration path (lines 186-191),
    SystemExit caught → ConfigurationError (lines 241-244)
  - ORBClient.cleanup(): container.clear() called, dynamic methods removed (lines 263-278)
  - ORBClient.get_stats(): pre-init dict (lines 398-403), post-init dict (lines 405-415)
  - ORBClient.batch(): not-initialized raises, empty list returns [], gather ordering (lines 417-442)
  - ORBClient.add_middleware(): post-init re-wrap called (lines 444-458)
  - ORBClient.list_return_requests(): not-initialized guard (line 990-991), success path (993-1014)
  - ORBClient.show_template(): not-initialized guard, delegates to get_template (lines 943-954)
  - ORBClient.get_template_or_none(): NotFoundError returns None (lines 956-961)
  - ORBClient.health_check(): not-initialized guard (lines 963-974)
  - ORBClient.wait_for_request(): not-initialized guard (lines 1174-1175),
    ValueError on status string treated as non-terminal → timeout (line 1192)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.sdk.client import ORBClient
from orb.sdk.exceptions import NotFoundError, SDKError

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_client(**kwargs) -> ORBClient:
    return ORBClient(config={"provider": "mock", **kwargs})


def _initialized_client() -> ORBClient:
    """Return a minimally-initialized ORBClient with all heavy deps mocked."""
    client = _make_client()
    client._initialized = True
    client._query_bus = AsyncMock()
    client._command_bus = AsyncMock()

    from orb.sdk.discovery import SDKMethodDiscovery

    client._discovery = SDKMethodDiscovery()
    client._methods = {}

    mock_container = MagicMock()
    mock_container.get_optional.return_value = None
    client._container = mock_container

    mock_app = MagicMock()
    mock_app.cleanup = AsyncMock()
    client._app = mock_app

    return client


# ---------------------------------------------------------------------------
# initialize() — config_path does not exist
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeConfigPathMissing:
    def test_missing_config_path_raises_configuration_error_during_initialize(self, tmp_path):
        from orb.sdk.exceptions import ConfigurationError

        # SDKConfig.from_file raises at construction time, but we can test
        # the initialize() guard path by providing config_path via the config dict
        # and having the file absent at initialize() time.
        client = ORBClient(config={"provider": "mock"})
        client._config.config_path = str(tmp_path / "nonexistent_config.json")

        with pytest.raises(ConfigurationError, match="Configuration file not found"):
            asyncio.run(client.initialize())


# ---------------------------------------------------------------------------
# initialize() — SystemExit caught
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeSystemExitCaught:
    def test_system_exit_converted_to_configuration_error(self, tmp_path):
        from orb.sdk.exceptions import ConfigurationError

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")
        client = ORBClient(config_path=str(cfg_file))

        with patch("orb.sdk.client.create_container") as mock_cc:
            mock_cc.side_effect = SystemExit(1)
            with pytest.raises(ConfigurationError, match="Configuration validation failed"):
                asyncio.run(client.initialize())


# ---------------------------------------------------------------------------
# cleanup()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanup:
    def test_cleanup_calls_container_clear(self):
        client = _initialized_client()
        mock_container = MagicMock()
        client._container = mock_container

        asyncio.run(client.cleanup())

        mock_container.clear.assert_called_once()
        assert client._container is None

    def test_cleanup_resets_initialized_flag(self):
        client = _initialized_client()
        asyncio.run(client.cleanup())
        assert client._initialized is False

    def test_cleanup_removes_dynamic_methods(self):
        client = _initialized_client()

        # Add a fake dynamic method
        setattr(client, "dynamic_method_xyz", lambda: None)
        client._methods["dynamic_method_xyz"] = lambda: None

        from orb.sdk.discovery import SDKMethodDiscovery

        disc = SDKMethodDiscovery()
        # Populate _method_info_cache so list_available_methods() returns our name
        disc._method_info_cache["dynamic_method_xyz"] = MagicMock()
        client._discovery = disc

        asyncio.run(client.cleanup())

        assert not hasattr(client, "dynamic_method_xyz")

    def test_cleanup_does_not_raise_on_app_cleanup_failure(self):
        client = _initialized_client()
        client._app.cleanup.side_effect = RuntimeError("cleanup failed")

        # Should not raise
        asyncio.run(client.cleanup())


# ---------------------------------------------------------------------------
# get_stats()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetStats:
    def test_pre_init_returns_not_initialized_dict(self):
        client = _make_client()
        stats = client.get_stats()
        assert stats["initialized"] is False
        assert stats["methods_discovered"] == 0

    def test_post_init_returns_method_counts(self):
        client = _initialized_client()
        client._methods = {"method_a": MagicMock(), "method_b": MagicMock()}

        from orb.sdk.discovery import MethodInfo, SDKMethodDiscovery

        disc = SDKMethodDiscovery()
        # Populate discovery with stub MethodInfo objects (all required fields)
        disc._method_info_cache = {
            "method_a": MethodInfo(
                name="method_a",
                description="",
                parameters={},
                required_params=[],
                return_type=None,
                handler_type="command",
                original_class=object,
            ),
            "method_b": MethodInfo(
                name="method_b",
                description="",
                parameters={},
                required_params=[],
                return_type=None,
                handler_type="query",
                original_class=object,
            ),
        }
        client._discovery = disc

        stats = client.get_stats()
        assert stats["initialized"] is True
        assert stats["methods_discovered"] == 2


# ---------------------------------------------------------------------------
# batch()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBatch:
    def test_not_initialized_raises_sdk_error(self):
        client = _make_client()
        with pytest.raises(SDKError):
            asyncio.run(client.batch([]))

    def test_empty_list_returns_empty_list(self):
        client = _initialized_client()
        result = asyncio.run(client.batch([]))
        assert result == []

    def test_results_returned_in_order(self):
        client = _initialized_client()

        async def op_a():
            return "a"

        async def op_b():
            return "b"

        results = asyncio.run(client.batch([op_a(), op_b()]))
        assert results == ["a", "b"]

    def test_exception_captured_at_index(self):
        client = _initialized_client()
        err = ValueError("fail")

        async def op_good():
            return "ok"

        async def op_bad():
            raise err

        results = asyncio.run(client.batch([op_good(), op_bad()]))
        assert results[0] == "ok"
        assert isinstance(results[1], ValueError)


# ---------------------------------------------------------------------------
# add_middleware()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAddMiddleware:
    def test_add_middleware_before_init_appended_to_list(self):
        client = _make_client()
        mw = MagicMock()
        client.add_middleware(mw)
        assert mw in client._middlewares

    def test_add_middleware_after_init_triggers_reapply(self):
        client = _initialized_client()
        client._methods = {"some_method": AsyncMock()}
        setattr(client, "some_method", client._methods["some_method"])

        mw = MagicMock()

        with patch.object(client, "_apply_middleware_to_methods") as mock_apply:
            client.add_middleware(mw)

        mock_apply.assert_called_once()


# ---------------------------------------------------------------------------
# list_return_requests() — not-initialized guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListReturnRequests:
    def test_not_initialized_raises_sdk_error(self):
        client = _make_client()
        with pytest.raises(SDKError):
            asyncio.run(client.list_return_requests())

    def test_initialized_calls_orchestrator_and_returns_result(self):
        client = _initialized_client()

        mock_result = MagicMock()
        mock_result.requests = [{"id": "r1"}]

        mock_orchestrator = MagicMock()
        mock_orchestrator.execute = AsyncMock(return_value=mock_result)

        client._container.get.return_value = mock_orchestrator
        client._container.get_optional.return_value = None  # no scheduler

        output = asyncio.run(client.list_return_requests())
        assert output == {"requests": [{"id": "r1"}]}

    def test_initialized_with_scheduler_formats_response(self):
        client = _initialized_client()

        mock_result = MagicMock()
        mock_result.requests = [{"id": "r1"}]

        mock_orchestrator = MagicMock()
        mock_orchestrator.execute = AsyncMock(return_value=mock_result)

        mock_scheduler = MagicMock()
        mock_scheduler.format_request_status_response.return_value = {"formatted": True}

        client._container.get.return_value = mock_orchestrator
        client._container.get_optional.return_value = mock_scheduler

        output = asyncio.run(client.list_return_requests())
        assert output == {"formatted": True}


# ---------------------------------------------------------------------------
# show_template()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestShowTemplate:
    def test_not_initialized_raises_sdk_error(self):
        client = _make_client()
        with pytest.raises(SDKError):
            asyncio.run(client.show_template("t1"))

    def test_delegates_to_get_template(self):
        client = _initialized_client()

        expected = {"template_id": "t1"}

        mock_result = MagicMock()
        mock_result.template = MagicMock()
        mock_result.template.to_dict.return_value = expected

        mock_orchestrator = MagicMock()
        mock_orchestrator.execute = AsyncMock(return_value=mock_result)

        client._container.get.return_value = mock_orchestrator
        client._container.get_optional.return_value = None

        result = asyncio.run(client.show_template("t1"))
        assert result == expected


# ---------------------------------------------------------------------------
# get_template_or_none()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTemplateOrNone:
    def test_returns_none_when_not_found(self):
        client = _initialized_client()

        with patch.object(
            client, "get_template", AsyncMock(side_effect=NotFoundError("Template", "t1"))
        ):
            result = asyncio.run(client.get_template_or_none(template_id="t1"))

        assert result is None

    def test_returns_template_when_found(self):
        client = _initialized_client()
        expected = {"template_id": "t1"}

        with patch.object(client, "get_template", AsyncMock(return_value=expected)):
            result = asyncio.run(client.get_template_or_none(template_id="t1"))

        assert result == expected


# ---------------------------------------------------------------------------
# health_check() convenience wrapper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthCheckConvenienceWrapper:
    def test_not_initialized_raises_sdk_error(self):
        client = _make_client()
        with pytest.raises(SDKError):
            asyncio.run(client.health_check())

    def test_delegates_to_get_provider_health(self):
        client = _initialized_client()
        health_result = {"status": "healthy"}

        with patch.object(client, "get_provider_health", AsyncMock(return_value=health_result)):
            result = asyncio.run(client.health_check())

        assert result == health_result


# ---------------------------------------------------------------------------
# wait_for_request() — not-initialized guard and ValueError path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWaitForRequest:
    def test_not_initialized_raises_sdk_error(self):
        client = _make_client()
        with pytest.raises(SDKError):
            asyncio.run(client.wait_for_request("req-1"))

    def test_invalid_status_string_treated_as_non_terminal_then_times_out(self):
        """ValueError on RequestStatus(status_str) should be caught; loop eventually times out."""
        from orb.sdk.exceptions import RequestTimeoutError

        client = _initialized_client()

        # Return an invalid/unknown status so the ValueError branch is hit
        status_result = {"requests": [{"status": "INVALID_STATUS_XYZ"}]}

        with patch.object(
            client,
            "get_request_status",
            AsyncMock(return_value=status_result),
        ):
            with pytest.raises(RequestTimeoutError):
                asyncio.run(client.wait_for_request("req-1", timeout=0.05, poll_interval=0.01))

    def test_terminal_status_returns_immediately(self):
        """A known terminal status ('complete') causes the loop to exit."""
        client = _initialized_client()

        # 'complete' is a valid terminal RequestStatus value
        status_result = {"requests": [{"status": "complete"}]}

        with patch.object(
            client,
            "get_request_status",
            AsyncMock(return_value=status_result),
        ):
            result = asyncio.run(client.wait_for_request("req-1", timeout=30.0, poll_interval=0.01))

        assert result == {"status": "complete"}


# ---------------------------------------------------------------------------
# cancel_request() — not-initialized guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelRequestGuard:
    def test_not_initialized_raises_sdk_error(self):
        client = _make_client()
        with pytest.raises(SDKError):
            asyncio.run(client.cancel_request("req-1"))

    def test_initialized_with_scheduler_formats_response(self):
        client = _initialized_client()

        mock_result = MagicMock()
        mock_result.request_id = "req-1"
        mock_result.status = "cancelled"

        mock_orchestrator = MagicMock()
        mock_orchestrator.execute = AsyncMock(return_value=mock_result)

        mock_scheduler = MagicMock()
        mock_scheduler.format_request_response.return_value = {"formatted": "cancel"}

        client._container.get.return_value = mock_orchestrator
        client._container.get_optional.return_value = mock_scheduler

        result = asyncio.run(client.cancel_request("req-1"))
        assert result == {"formatted": "cancel"}


# ---------------------------------------------------------------------------
# return_machines() — scheduler formatting branch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReturnMachinesSchedulerBranch:
    def test_scheduler_formats_response(self):
        client = _initialized_client()

        mock_result = MagicMock()
        mock_result.request_id = "ret-1"
        mock_result.status = "returning"
        mock_result.message = "OK"
        mock_result.skipped_machines = []

        mock_orchestrator = MagicMock()
        mock_orchestrator.execute = AsyncMock(return_value=mock_result)

        mock_scheduler = MagicMock()
        mock_scheduler.format_request_response.return_value = {"formatted": "return"}

        client._container.get.return_value = mock_orchestrator
        client._container.get_optional.return_value = mock_scheduler

        result = asyncio.run(client.return_machines(["m1", "m2"]))
        assert result == {"formatted": "return"}
