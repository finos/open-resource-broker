"""Unit tests for ORBClient covering uncovered lines.

Targets: sdk/client.py lines 123, 125, 171, 236, 263, 321, 458, 462-464,
524, 540, 546-547, 566, 599-601, 603-605, 609-610, 619-620, 626-628, 632-634,
636-637, 640, 642-643, 649-653, 682, 699-704, 709, 733, 761-763, 814, 861,
879-880, 889, 894, 920, 941, 958-961, 991, 1014, 1192.

Key areas:
- Deprecated region/profile kwargs → DeprecationWarning
- config_path resolution in initialize()
- cleanup() — container.clear(), dynamic method removal
- batch() — not-initialized guard, empty list, gather ordering
- add_middleware() after initialization
- list_available_methods() / get_method_info() / get_methods_by_type() not-initialized guards
- get_stats() — pre/post init
- explicit orchestrator methods not-initialized guard
- NotFoundError raised when result.machine / result.template is None
"""

from __future__ import annotations

import asyncio
import warnings
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.sdk.client import ORBClient
from orb.sdk.exceptions import NotFoundError, SDKError

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_client(**kwargs) -> ORBClient:
    """Create ORBClient with minimal config to avoid env-var side effects."""
    return ORBClient(config={"provider": "mock", **kwargs})


def _initialized_client() -> ORBClient:
    """Return an ORBClient already marked initialized with mock internals."""
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
# Deprecated region/profile kwargs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeprecatedRegionProfile:
    def test_region_kwarg_emits_deprecation_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ORBClient(config={"provider": "mock"}, region="us-east-1")
        assert any(issubclass(w.category, DeprecationWarning) for w in caught)

    def test_profile_kwarg_emits_deprecation_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ORBClient(config={"provider": "mock"}, profile="my-profile")
        assert any(issubclass(w.category, DeprecationWarning) for w in caught)

    def test_region_forwarded_to_provider_config(self):
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            client = ORBClient(config={"provider": "mock"}, region="eu-west-1")
        assert client._config.provider_config.get("region") == "eu-west-1"

    def test_profile_forwarded_to_provider_config(self):
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            client = ORBClient(config={"provider": "mock"}, profile="dev")
        assert client._config.provider_config.get("profile") == "dev"

    def test_existing_provider_config_merged_not_overwritten(self):
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            client = ORBClient(
                config={"provider": "mock"},
                provider_config={"existing_key": "val"},
                region="ap-southeast-1",
            )
        assert client._config.provider_config.get("region") == "ap-southeast-1"
        assert client._config.provider_config.get("existing_key") == "val"


# ---------------------------------------------------------------------------
# initialize() — config_path not found guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeConfigPath:
    def test_missing_config_path_raises_configuration_error(self, tmp_path):
        from orb.sdk.exceptions import ConfigurationError

        client = ORBClient(config={"provider": "mock"})
        client._config.config_path = str(tmp_path / "missing.json")

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(ConfigurationError, match="Configuration file not found"):
                loop.run_until_complete(client.initialize())
        finally:
            loop.close()

    def test_already_initialized_returns_true(self):
        client = _initialized_client()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(client.initialize())
        finally:
            loop.close()
        assert result is True


# ---------------------------------------------------------------------------
# batch()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBatch:
    def test_not_initialized_raises_sdk_error(self):
        client = _make_client()
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(SDKError, match="not initialized"):
                loop.run_until_complete(client.batch([MagicMock()]))
        finally:
            loop.close()

    def test_empty_operations_returns_empty_list(self):
        client = _initialized_client()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(client.batch([]))
        finally:
            loop.close()
        assert result == []

    def test_results_returned_in_order(self):
        client = _initialized_client()

        async def _make_coros():
            async def _a():
                return "first"

            async def _b():
                return "second"

            return await client.batch([_a(), _b()])

        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(_make_coros())
        finally:
            loop.close()
        assert results == ["first", "second"]

    def test_failed_operation_captured_as_exception_in_result(self):
        client = _initialized_client()

        async def _failing():
            raise ValueError("boom")

        async def _run():
            return await client.batch([_failing()])

        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(_run())
        finally:
            loop.close()
        assert isinstance(results[0], ValueError)


# ---------------------------------------------------------------------------
# list_available_methods / get_method_info / get_methods_by_type — not-init guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIntrospectionNotInitialized:
    def test_list_available_methods_raises_when_not_initialized(self):
        client = _make_client()
        with pytest.raises(SDKError, match="not initialized"):
            client.list_available_methods()

    def test_get_method_info_raises_when_not_initialized(self):
        client = _make_client()
        with pytest.raises(SDKError, match="not initialized"):
            client.get_method_info("list_templates")

    def test_get_methods_by_type_raises_when_not_initialized(self):
        client = _make_client()
        with pytest.raises(SDKError, match="not initialized"):
            client.get_methods_by_type("query")

    def test_get_method_parameters_raises_when_not_initialized(self):
        client = _make_client()
        with pytest.raises(SDKError, match="not initialized"):
            client.get_method_parameters("list_templates")


# ---------------------------------------------------------------------------
# list_available_methods / get_method_info — initialized path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIntrospectionInitialized:
    def test_list_available_methods_includes_explicit_methods(self):
        client = _initialized_client()
        methods = client.list_available_methods()
        assert "list_templates" in methods
        assert "request_machines" in methods

    def test_get_method_info_returns_none_for_unknown(self):
        client = _initialized_client()
        result = client.get_method_info("totally_unknown_method_xyz")
        assert result is None

    def test_get_methods_by_type_returns_list(self):
        client = _initialized_client()
        result = client.get_methods_by_type("query")
        assert isinstance(result, list)

    def test_get_stats_pre_init(self):
        client = _make_client()
        stats = client.get_stats()
        assert stats["initialized"] is False
        assert stats["methods_discovered"] == 0

    def test_get_stats_post_init(self):
        client = _initialized_client()
        stats = client.get_stats()
        assert stats["initialized"] is True
        assert "available_methods" in stats


# ---------------------------------------------------------------------------
# add_middleware() — after initialization
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAddMiddlewareAfterInit:
    def test_add_middleware_before_init_stores_it(self):
        from orb.sdk.middleware import SDKMiddleware

        client = _make_client()
        mw = MagicMock(spec=SDKMiddleware)
        client.add_middleware(mw)
        assert mw in client._middlewares

    def test_add_middleware_after_init_reapplies(self):
        from orb.sdk.middleware import SDKMiddleware

        client = _initialized_client()
        # Add a discovered method so _apply_middleware_to_methods has something to wrap
        called = []

        async def _raw(**kwargs):
            return {"ok": True}

        client._methods["test_method"] = _raw
        # Set the method on the instance too (as initialize() would)
        setattr(client, "test_method", _raw)

        mw = MagicMock(spec=SDKMiddleware)

        # build_middleware_chain is imported into client module namespace
        import orb.sdk.client as client_module

        orig_build = client_module.build_middleware_chain

        def _spy_build(middlewares, name, fn):
            called.append(name)
            return fn

        client_module.build_middleware_chain = _spy_build
        try:
            client.add_middleware(mw)
        finally:
            client_module.build_middleware_chain = orig_build

        assert "test_method" in called


# ---------------------------------------------------------------------------
# cleanup()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanup:
    def test_cleanup_resets_initialized_flag(self):
        client = _initialized_client()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(client.cleanup())
        finally:
            loop.close()
        assert client._initialized is False

    def test_cleanup_clears_methods(self):
        client = _initialized_client()
        client._methods["foo"] = lambda: None

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(client.cleanup())
        finally:
            loop.close()
        assert client._methods == {}

    def test_cleanup_calls_container_clear(self):
        client = _initialized_client()
        mock_container = MagicMock()
        mock_container.get_optional.return_value = None
        client._container = mock_container

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(client.cleanup())
        finally:
            loop.close()
        mock_container.clear.assert_called_once()
        assert client._container is None


# ---------------------------------------------------------------------------
# Explicit orchestrator methods — not-initialized guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExplicitMethodsNotInit:
    def test_request_machines_not_init_raises(self):
        client = _make_client()
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(SDKError, match="not initialized"):
                loop.run_until_complete(client.request_machines("tmpl", 1))
        finally:
            loop.close()

    def test_get_request_status_not_init_raises(self):
        client = _make_client()
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(SDKError, match="not initialized"):
                loop.run_until_complete(client.get_request_status(["req-1"]))
        finally:
            loop.close()

    def test_return_machines_not_init_raises(self):
        client = _make_client()
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(SDKError, match="not initialized"):
                loop.run_until_complete(client.return_machines(["m-1"]))
        finally:
            loop.close()

    def test_cancel_request_not_init_raises(self):
        client = _make_client()
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(SDKError, match="not initialized"):
                loop.run_until_complete(client.cancel_request("req-1"))
        finally:
            loop.close()

    def test_list_machines_not_init_raises(self):
        client = _make_client()
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(SDKError, match="not initialized"):
                loop.run_until_complete(client.list_machines())
        finally:
            loop.close()

    def test_get_machine_not_init_raises(self):
        client = _make_client()
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(SDKError, match="not initialized"):
                loop.run_until_complete(client.get_machine("m-1"))
        finally:
            loop.close()

    def test_list_templates_not_init_raises(self):
        client = _make_client()
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(SDKError, match="not initialized"):
                loop.run_until_complete(client.list_templates())
        finally:
            loop.close()

    def test_get_template_not_init_raises(self):
        client = _make_client()
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(SDKError, match="not initialized"):
                loop.run_until_complete(client.get_template("tmpl-1"))
        finally:
            loop.close()

    def test_list_requests_not_init_raises(self):
        client = _make_client()
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(SDKError, match="not initialized"):
                loop.run_until_complete(client.list_requests())
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# NotFoundError raised when orchestrator returns None machine / template
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNotFoundErrors:
    def _client_with_container(self):
        client = _initialized_client()
        return client

    def test_get_machine_raises_not_found_when_machine_is_none(self):
        client = self._client_with_container()

        mock_result = MagicMock()
        mock_result.machine = None
        mock_orchestrator = AsyncMock()
        mock_orchestrator.execute = AsyncMock(return_value=mock_result)
        client._container.get.return_value = mock_orchestrator  # type: ignore[union-attr]

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(NotFoundError):
                loop.run_until_complete(client.get_machine("ghost-machine"))
        finally:
            loop.close()

    def test_get_template_raises_not_found_when_template_is_none(self):
        client = self._client_with_container()

        mock_result = MagicMock()
        mock_result.template = None
        mock_orchestrator = AsyncMock()
        mock_orchestrator.execute = AsyncMock(return_value=mock_result)
        client._container.get.return_value = mock_orchestrator  # type: ignore[union-attr]

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(NotFoundError):
                loop.run_until_complete(client.get_template("ghost-template"))
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# get_request_status — request_id singular alias
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetRequestStatusAliases:
    def test_request_id_singular_merged_into_list(self):
        client = _initialized_client()

        captured = {}

        async def _execute(input_dto):
            captured["ids"] = input_dto.request_ids
            result = MagicMock()
            result.requests = []
            return result

        mock_orchestrator = AsyncMock()
        mock_orchestrator.execute = _execute
        client._container.get.return_value = mock_orchestrator  # type: ignore[union-attr]
        client._container.get_optional.return_value = None  # type: ignore[union-attr]

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(client.get_request_status(request_id="single-id"))
        finally:
            loop.close()

        assert "single-id" in captured["ids"]

    def test_request_id_not_duplicated_when_already_in_list(self):
        client = _initialized_client()

        captured = {}

        async def _execute(input_dto):
            captured["ids"] = input_dto.request_ids
            result = MagicMock()
            result.requests = []
            return result

        mock_orchestrator = AsyncMock()
        mock_orchestrator.execute = _execute
        client._container.get.return_value = mock_orchestrator  # type: ignore[union-attr]
        client._container.get_optional.return_value = None  # type: ignore[union-attr]

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                client.get_request_status(request_ids=["single-id"], request_id="single-id")
            )
        finally:
            loop.close()

        assert captured["ids"].count("single-id") == 1
