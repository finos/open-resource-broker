"""Tests for the CycleCloud handler and related template/exception additions."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.exceptions.azure_exceptions import (
    CycleCloudConnectionError,
    CycleCloudNodeError,
    TerminationError,
)
from orb.providers.azure.infrastructure.cyclecloud_session import (
    CycleCloudCredentialData,
    CycleCloudRequestContext,
)
from orb.providers.azure.infrastructure.cyclecloud_session_builder import (
    CycleCloudSessionBuilder,
)
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureReleaseContext
from orb.providers.azure.infrastructure.handlers.cyclecloud_handler import (
    CycleCloudHandler,
    resolve_cc_state,
)
from tests.providers.azure.strategy_test_support import make_azure_template, run_operation

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_CC_TEMPLATE_FIELDS = {
    "template_id": "cc-test",
    "vm_size": "Standard_D4s_v5",
    "resource_group": "test-rg",
    "location": "eastus2",
    "provider_api": "CycleCloud",
    "cluster_name": "my-cluster",
    "node_array": "execute",
    "cyclecloud_url": "https://cc.example.com",
    "cyclecloud_auth_mode": "bearer",
    "cyclecloud_aad_scope": "https://cc.example.com/.default",
    "cyclecloud_verify_ssl": False,
}


def _make_template(**overrides):
    return make_azure_template(**{**_CC_TEMPLATE_FIELDS, **overrides})


def _make_handler():
    azure_client = MagicMock()
    azure_client.get_provider_config.return_value = None
    azure_client.get_async_credential = AsyncMock(return_value=None)
    logger = MagicMock()
    return CycleCloudHandler(azure_client=azure_client, logger=logger)


def _make_request(count=2, resource_ids=None, metadata=None):
    req = MagicMock()
    req.request_id = "req-12345678-1234-1234-1234-123456789012"
    req.requested_count = count
    req.resource_ids = resource_ids or []
    req.metadata = metadata or {}
    return req


def _make_cc_request_context(**values):
    return CycleCloudRequestContext.from_mapping(values)


class _AsyncContextManager:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _make_async_session_context(
    *,
    base_url: str = "https://cc.example.com",
    credential_path: str | None = None,
    verify_ssl: bool = False,
    auth_mode: str | None = "bearer",
):
    session_context = MagicMock()
    session_context.client = object()
    session_context.base_url = base_url
    session_context.credential_path = credential_path
    session_context.verify_ssl = verify_ssl
    session_context.auth_mode = auth_mode
    return session_context


def _wire_async_cyclecloud_calls(
    handler: CycleCloudHandler,
    *,
    responses: list[dict[str, object]],
    session_context=None,
):
    session_context = session_context or _make_async_session_context()
    handler._async_cc_session_scope = MagicMock(return_value=_AsyncContextManager(session_context))
    handler._cc_request_async = AsyncMock(side_effect=responses)
    return session_context


@pytest.mark.asyncio
async def test_cc_request_async_wraps_invalid_json_with_connection_error():
    handler = _make_handler()
    response = MagicMock()
    response.request.url = "https://cc.example.com/api/nodes"
    response.headers = {"Content-Type": "application/json"}
    response.status_code = 200
    response.content = b"{invalid"
    response.raise_for_status = MagicMock()
    response.json.side_effect = json.JSONDecodeError("bad json", "{invalid", 1)

    client = MagicMock()
    client.request = AsyncMock(return_value=response)

    with pytest.raises(CycleCloudConnectionError, match="invalid JSON"):
        await handler._cc_request_async(
            client,
            "GET",
            "https://cc.example.com/api/nodes",
            include_metadata=True,
        )


# ---------------------------------------------------------------------------
# AzureTemplate CycleCloud fields
# ---------------------------------------------------------------------------


class TestCycleCloudTemplate:
    def test_cyclecloud_template_omitted_verify_ssl_is_unset(self):
        fields = {**_CC_TEMPLATE_FIELDS}
        del fields["cyclecloud_verify_ssl"]
        t = AzureTemplate(**fields)
        assert t.cyclecloud_verify_ssl is None

    def test_cyclecloud_template_accepts_explicit_verify_ssl_true(self):
        t = _make_template(cyclecloud_verify_ssl=True)
        assert t.cyclecloud_verify_ssl is True

    def test_cyclecloud_template_accepts_credential_path(self):
        t = _make_template(
            cyclecloud_auth_mode=None,
            cyclecloud_credential_path="config/cyclecloud-credentials.json",
        )
        assert t.cyclecloud_credential_path == "config/cyclecloud-credentials.json"

    def test_cyclecloud_template_accepts_aad_scope_auth_fields(self):
        t = _make_template(
            cyclecloud_auth_mode="bearer",
            cyclecloud_aad_scope="https://example/.default",
        )
        assert t.cyclecloud_auth_mode == "bearer"
        assert t.cyclecloud_aad_scope == "https://example/.default"

    def test_cyclecloud_template_rejects_inline_bearer_token_field(self):
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            _make_template(cyclecloud_bearer_token="token-123")

    def test_cyclecloud_template_rejects_inline_basic_auth_fields(self):
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            _make_template(cyclecloud_username="admin")

        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            _make_template(cyclecloud_password="secret")

    def test_cyclecloud_template_requires_cluster_name(self):
        with pytest.raises(ValueError, match="cluster_name is required"):
            _make_template(cluster_name=None)

    def test_cyclecloud_default_node_array(self):
        fields = {**_CC_TEMPLATE_FIELDS}
        del fields["node_array"]
        t = AzureTemplate(**fields)
        assert t.node_array == "execute"

    def test_template_rejects_unknown_extra_fields(self):
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            _make_template(made_up_setting=True)


# ---------------------------------------------------------------------------
# State mapping
# ---------------------------------------------------------------------------


class TestStateMapping:
    @pytest.mark.parametrize(
        "cc_state,expected",
        [
            ("Off", "stopped"),
            ("Acquiring", "pending"),
            ("Preparing", "pending"),
            ("Starting", "pending"),
            ("Software Configuration", "pending"),
            ("Ready", "running"),
            ("Deallocating", "shutting-down"),
            ("Deallocated", "stopped"),
            ("Terminated", "terminated"),
            ("Failed", "failed"),
            ("SomeUnknownState", "unknown"),
        ],
    )
    def test_resolve_cc_state(self, cc_state, expected):
        assert resolve_cc_state(cc_state) == expected


# ---------------------------------------------------------------------------
# CycleCloudHandler - acquire_hosts
# ---------------------------------------------------------------------------


class TestCycleCloudHandlerAcquire:
    def test_acquire_hosts_returns_submitted_operation_metadata(self):
        handler = _make_handler()
        template = _make_template()
        request = _make_request(count=2)
        session_context = _make_async_session_context(
            credential_path="config/cc.json",
            verify_ssl=False,
            auth_mode="bearer",
        )
        _wire_async_cyclecloud_calls(
            handler,
            session_context=session_context,
            responses=[
                {"state": "Started"},
                {
                    "body": {
                        "operationId": "op-123",
                        "sets": [
                            {
                                "added": 2,
                                "nodes": [
                                    {"name": "node-1", "status": "Acquiring"},
                                    {"name": "node-2", "status": "Acquiring"},
                                ],
                            }
                        ],
                    },
                    "headers": {"Location": "https://cc.example.com/operations/op-123"},
                },
            ],
        )

        result = run_operation(handler.acquire_hosts_async(request, template))

        assert result["success"] is True
        assert result["resource_ids"] == ["req-12345678-1234-1234-1234-123456789012"]
        assert result["provider_data"]["cluster_name"] == "my-cluster"
        assert result["provider_data"]["operation_id"] == "op-123"
        assert (
            result["provider_data"]["operation_location"]
            == "https://cc.example.com/operations/op-123"
        )
        assert result["provider_data"]["added_count"] == 2
        assert result["provider_data"]["submitted_count"] == 2
        assert result["provider_data"]["operation_status"] == "submitted"
        request_json = handler._cc_request_async.await_args_list[1].kwargs["json"]
        assert request_json["requestId"] == "req-12345678-1234-1234-1234-123456789012"

    @pytest.mark.asyncio
    async def test_acquire_hosts_async_returns_submitted_operation_metadata(self):
        handler = _make_handler()
        template = _make_template()
        request = _make_request(count=2)
        session_context = MagicMock()
        session_context.client = object()
        session_context.base_url = "https://cc.example.com"
        session_context.credential_path = "config/cc.json"
        session_context.verify_ssl = False
        session_context.auth_mode = "bearer"
        handler._async_cc_session_scope = MagicMock(
            return_value=_AsyncContextManager(session_context)
        )
        handler._cc_request_async = AsyncMock(
            side_effect=[
                {"state": "Started"},
                {
                    "body": {
                        "operationId": "op-123",
                        "sets": [
                            {"added": 2, "nodes": [{"name": "node-1", "status": "Acquiring"}]}
                        ],
                    },
                    "headers": {"Location": "https://cc.example.com/operations/op-123"},
                },
            ]
        )

        result = await handler.acquire_hosts_async(request, template)

        assert result["success"] is True
        assert result["provider_data"]["operation_id"] == "op-123"
        assert result["provider_data"]["cyclecloud_auth_mode"] == "bearer"
        assert result["provider_data"]["submitted_count"] == 2

    def test_acquire_hosts_missing_cluster_name(self):
        """Should raise CycleCloudNodeError if cluster_name is missing."""
        handler = _make_handler()
        # Bypass template validation by setting cluster_name to a truthy value
        # then overriding via object.__setattr__
        template = _make_template()
        object.__setattr__(template, "cluster_name", None)
        request = _make_request()

        with pytest.raises(CycleCloudNodeError, match="cluster_name is required"):
            run_operation(handler.acquire_hosts_async(request, template))

    def test_acquire_hosts_missing_url(self):
        """Should raise CycleCloudConnectionError if cyclecloud_url is missing."""
        handler = _make_handler()
        template = _make_template()
        object.__setattr__(template, "cyclecloud_url", None)
        request = _make_request()

        with pytest.raises(CycleCloudConnectionError, match="cyclecloud_url is required"):
            run_operation(handler.acquire_hosts_async(request, template))


# ---------------------------------------------------------------------------
# CycleCloudHandler - check_hosts_status
# ---------------------------------------------------------------------------


class TestCycleCloudHandlerStatus:
    def test_check_hosts_status_returns_filtered_results(self):
        handler = _make_handler()
        _wire_async_cyclecloud_calls(
            handler,
            responses=[
                {
                    "nodes": [
                        {
                            "name": "node-1",
                            "nodeId": "id-1",
                            "nodeArray": "execute",
                            "state": "Ready",
                            "privateIp": "10.0.0.1",
                            "machineType": "Standard_D4s_v5",
                        },
                        {
                            "name": "node-2",
                            "nodeId": "id-2",
                            "nodeArray": "execute",
                            "state": "Preparing",
                            "privateIp": "10.0.0.2",
                            "machineType": "Standard_D4s_v5",
                        },
                    ]
                }
            ],
        )

        request = _make_request(
            resource_ids=["req-12345678-1234-1234-1234-123456789012"],
            metadata={
                "cluster_name": "my-cluster",
                "node_array": "execute",
                "cyclecloud_url": "https://cc.example.com",
                "cyclecloud_auth_mode": "bearer",
                "cyclecloud_aad_scope": "https://cc.example.com/.default",
            },
        )

        results = run_operation(handler.check_hosts_status_async(request))

        assert len(results) == 2
        assert results[0]["instance_id"] == "node-1"
        assert results[0]["name"] == "node-1"
        assert results[0]["resource_id"] == "my-cluster"
        assert results[0]["status"] == "running"
        assert results[0]["private_ip"] == "10.0.0.1"
        assert results[1]["status"] == "pending"

    def test_check_hosts_status_no_cc_url(self):
        handler = _make_handler()
        request = _make_request(
            resource_ids=["req-12345678-1234-1234-1234-123456789012"],
            metadata={"cluster_name": "my-cluster"},
        )
        with pytest.raises(CycleCloudConnectionError, match="cyclecloud_url is required"):
            run_operation(handler.check_hosts_status_async(request))

    def test_check_hosts_status_requires_cyclecloud_request_identity(self):
        handler = _make_handler()
        request = _make_request(
            resource_ids=[""],
            metadata={
                "cluster_name": "my-cluster",
                "cyclecloud_url": "https://cc.example.com",
            },
        )
        with pytest.raises(CycleCloudConnectionError, match="request identity is required"):
            run_operation(handler.check_hosts_status_async(request))

    def test_check_hosts_status_requires_cluster_name(self):
        handler = _make_handler()
        request = _make_request(
            resource_ids=["req-12345678-1234-1234-1234-123456789012"],
            metadata={"cyclecloud_url": "https://cc.example.com"},
        )

        with pytest.raises(CycleCloudConnectionError, match="cluster_name is required"):
            run_operation(handler.check_hosts_status_async(request))

    def test_check_hosts_status_request_failure_raises(self):
        handler = _make_handler()
        _wire_async_cyclecloud_calls(
            handler,
            responses=[
                CycleCloudConnectionError("Cannot connect to CycleCloud at x: boom", url="x")
            ],
        )
        handler._cc_request_async.side_effect = CycleCloudConnectionError(
            "Cannot connect to CycleCloud at https://cc.example.com: boom",
            url="https://cc.example.com",
        )

        request = _make_request(
            resource_ids=["req-12345678-1234-1234-1234-123456789012"],
            metadata={
                "cluster_name": "my-cluster",
                "cyclecloud_url": "https://cc.example.com",
            },
        )

        with pytest.raises(CycleCloudConnectionError, match="Cannot connect to CycleCloud"):
            run_operation(handler.check_hosts_status_async(request))

        assert handler._logger.error.call_count == 1
        assert handler._logger.error.call_args.args[0] == (
            "Failed to build CycleCloud session for status check (cluster '%s'): %s"
        )
        assert handler._logger.error.call_args.args[1] == "my-cluster"

    @pytest.mark.asyncio
    async def test_check_hosts_status_async_returns_filtered_results(self):
        handler = _make_handler()
        session_context = MagicMock()
        session_context.client = object()
        session_context.base_url = "https://cc.example.com"
        handler._async_cc_session_scope = MagicMock(
            return_value=_AsyncContextManager(session_context)
        )
        handler._cc_request_async = AsyncMock(
            return_value={
                "nodes": [
                    {
                        "name": "node-1",
                        "nodeId": "id-1",
                        "nodeArray": "execute",
                        "state": "Ready",
                        "machineType": "Standard_D4s_v5",
                    }
                ]
            }
        )
        request = _make_request(
            resource_ids=["req-123"],
            metadata={
                "cluster_name": "my-cluster",
                "node_array": "execute",
                "node_ids": ["node-1"],
                "cyclecloud_url": "https://cc.example.com",
            },
        )

        result = await handler.check_hosts_status_async(request)

        assert len(result) == 1
        assert result[0]["status"] == "running"
        assert result[0]["instance_id"] == "node-1"


# ---------------------------------------------------------------------------
# CycleCloudHandler - release_hosts
# ---------------------------------------------------------------------------


class TestCycleCloudHandlerRelease:
    def test_release_hosts_returns_submitted_release_metadata(self):
        handler = _make_handler()
        _wire_async_cyclecloud_calls(
            handler,
            responses=[
                {"nodes": [{"name": "node-1", "nodeId": "node-1"}]},
                {
                    "body": {"operationId": "op-release"},
                    "headers": {"Location": "https://cc.example.com/operations/op-release"},
                },
            ],
        )

        result = run_operation(
            handler.release_hosts_async(
                machine_ids=["node-1", "node-2"],
                resource_id="my-cluster",
                context=AzureReleaseContext(
                    cyclecloud_request_context=CycleCloudRequestContext(
                        cyclecloud_url="https://cc.example.com",
                        cyclecloud_auth_mode="bearer",
                        cyclecloud_aad_scope="https://cc.example.com/.default",
                    )
                ),
            )
        )
        assert result["provider_data"]["operation_status"] == "submitted"
        assert result["provider_data"]["terminate_operation_location"] == (
            "https://cc.example.com/operations/op-release"
        )

    def test_release_hosts_missing_url(self):
        handler = _make_handler()
        with pytest.raises(TerminationError, match="cyclecloud_url is required"):
            run_operation(
                handler.release_hosts_async(
                    machine_ids=["node-1"],
                    resource_id="my-cluster",
                    context=AzureReleaseContext(),
                )
            )

    @pytest.mark.asyncio
    async def test_release_hosts_async_returns_submitted_release_metadata(self):
        handler = _make_handler()
        session_context = MagicMock()
        session_context.client = object()
        session_context.base_url = "https://cc.example.com"
        handler._async_cc_session_scope = MagicMock(
            return_value=_AsyncContextManager(session_context)
        )
        handler._resolve_release_node_targets_async = AsyncMock(return_value={"names": ["node-1"]})
        handler._cc_request_async = AsyncMock(
            return_value={
                "body": {"operationId": "op-release"},
                "headers": {"Location": "https://cc.example.com/operations/op-release"},
            }
        )

        result = await handler.release_hosts_async(
            machine_ids=["node-1"],
            resource_id="my-cluster",
            context=AzureReleaseContext(
                cyclecloud_request_context=_make_cc_request_context(
                    cluster_name="my-cluster",
                    cyclecloud_url="https://cc.example.com",
                )
            ),
        )

        assert result is not None
        assert result["provider_data"]["operation_status"] == "submitted"
        assert (
            result["provider_data"]["terminate_operation_location"]
            == "https://cc.example.com/operations/op-release"
        )


# ---------------------------------------------------------------------------
# CycleCloudHandler - auth modes
# ---------------------------------------------------------------------------


class TestCycleCloudAuthModes:
    @pytest.mark.asyncio
    async def test_cc_request_async_uses_provider_configured_timeouts(self):
        handler = _make_handler()
        handler.azure_client.get_provider_config.return_value = AzureProviderConfig(
            region="eastus2",
            connect_timeout=5,
            read_timeout=13,
        )
        client = MagicMock()
        response = MagicMock()
        response.content = b"{}"
        response.json.return_value = {}
        response.raise_for_status = MagicMock()
        client.request = AsyncMock(return_value=response)

        await handler._cc_request_async(
            client,
            "GET",
            "https://cc.example.com/clusters/my-cluster/status",
        )

        client.request.assert_awaited_once_with(
            "GET",
            "https://cc.example.com/clusters/my-cluster/status",
        )

    @pytest.mark.asyncio
    async def test_build_async_cc_session_uses_async_credential(self):
        handler = _make_handler()
        async_credential = MagicMock()
        async_credential.get_token = AsyncMock(return_value=MagicMock(token="tok-123"))
        handler.azure_client.get_async_credential = AsyncMock(return_value=async_credential)

        class _CredentialProperty:
            def __get__(self, instance, owner):
                raise AssertionError("sync credential should not be used")

        with patch.object(
            type(handler.azure_client),
            "credential",
            new=_CredentialProperty(),
            create=True,
        ):
            session_context = await handler._build_async_cc_session(
                cc_url="https://cc.example.com",
                verify_ssl=False,
                request_context=_make_cc_request_context(cyclecloud_auth_mode="bearer"),
            )

        assert session_context.auth_mode == "bearer"
        await session_context.client.aclose()

    @pytest.mark.asyncio
    async def test_build_async_cc_session_reads_timeout_tuple_once(self):
        handler = _make_handler()
        handler._get_cc_request_timeout = MagicMock(return_value=(5, 13))
        handler.azure_client.get_async_credential = AsyncMock(return_value=None)

        with patch.object(
            CycleCloudSessionBuilder,
            "resolve_async_auth",
            new=AsyncMock(return_value=({}, None, "none")),
        ):
            session_context = await handler._build_async_cc_session(
                cc_url="https://cc.example.com",
                verify_ssl=False,
                request_context=_make_cc_request_context(),
            )

        handler._get_cc_request_timeout.assert_called_once_with()
        await session_context.client.aclose()

    @pytest.mark.asyncio
    async def test_build_async_cc_session_uses_azure_bearer_when_no_basic_auth(self):
        handler = _make_handler()
        async_credential = MagicMock()
        async_credential.get_token = AsyncMock(return_value=MagicMock(token="tok-123"))
        handler.azure_client.get_async_credential = AsyncMock(return_value=async_credential)

        session_context = await handler._build_async_cc_session(
            cc_url="https://cc.example.com",
            verify_ssl=True,
            request_context=_make_cc_request_context(cyclecloud_auth_mode="bearer"),
        )

        assert session_context.base_url == "https://cc.example.com"
        assert session_context.auth_mode == "bearer"
        assert session_context.client.headers["Authorization"] == "Bearer tok-123"
        await session_context.client.aclose()

    @pytest.mark.asyncio
    async def test_build_async_cc_session_rejects_ssh_auth_mode(self):
        handler = _make_handler()

        with pytest.raises(
            CycleCloudConnectionError,
            match="cyclecloud_auth_mode=ssh is not supported",
        ):
            await handler._build_async_cc_session(
                cc_url="https://cc.example.com",
                verify_ssl=True,
                request_context=_make_cc_request_context(cyclecloud_auth_mode="ssh"),
            )

    @pytest.mark.asyncio
    async def test_build_async_cc_session_propagates_auth_resolution_failures(self):
        handler = _make_handler()

        with patch.object(
            CycleCloudSessionBuilder,
            "resolve_async_auth",
            new=AsyncMock(
                side_effect=CycleCloudConnectionError(
                    "cyclecloud_auth_mode=bearer requested but no bearer token could be resolved"
                )
            ),
        ):
            with pytest.raises(
                CycleCloudConnectionError,
                match="cyclecloud_auth_mode=bearer requested but no bearer token could be resolved",
            ):
                await handler._build_async_cc_session(
                    cc_url="https://cc.example.com",
                    verify_ssl=True,
                    request_context=_make_cc_request_context(cyclecloud_auth_mode="bearer"),
                )

    @pytest.mark.asyncio
    async def test_build_async_cc_session_propagates_client_construction_failures(self):
        handler = _make_handler()

        with (
            patch.object(
                CycleCloudSessionBuilder,
                "resolve_async_auth",
                new=AsyncMock(return_value=({}, None, "none")),
            ),
            patch(
                "orb.providers.azure.infrastructure.handlers.cyclecloud_handler.httpx.AsyncClient",
                side_effect=RuntimeError("client setup failed"),
            ),
        ):
            with pytest.raises(RuntimeError, match="client setup failed"):
                await handler._build_async_cc_session(
                    cc_url="https://cc.example.com",
                    verify_ssl=True,
                    request_context=_make_cc_request_context(cyclecloud_auth_mode="none"),
                )

    @pytest.mark.asyncio
    async def test_async_cc_session_scope_builds_session_on_enter(self):
        handler = _make_handler()

        with patch.object(
            CycleCloudSessionBuilder,
            "resolve_async_auth",
            new=AsyncMock(return_value=({}, None, "none")),
        ):
            async with handler._async_cc_session_scope(
                cc_url="https://cc.example.com",
                verify_ssl=True,
                request_context=_make_cc_request_context(cyclecloud_auth_mode="none"),
            ) as session_context:
                assert session_context.base_url == "https://cc.example.com"
                client = session_context.client
                assert client.is_closed is False

        assert client.is_closed is True

    @pytest.mark.asyncio
    async def test_build_async_cc_session_loads_cyclecloud_config_from_provider(self):
        handler = _make_handler()
        handler.azure_client.get_provider_config.return_value = AzureProviderConfig(
            region="eastus2",
            resource_group="orb-test-rg",
            cyclecloud={
                "credential_path": "config/cyclecloud-credentials.json",
                "url": "https://cc.example.com",
                "verify_ssl": False,
            },
        )
        with patch.object(
            CycleCloudSessionBuilder,
            "_load_credential_file",
            return_value=CycleCloudCredentialData(
                username="cc_admin",
                password="changeme",
            ),
        ):
            session_context = await handler._build_async_cc_session(
                cc_url=None,
                verify_ssl=None,
            )

        assert session_context.base_url == "https://cc.example.com"
        assert session_context.verify_ssl is False
        assert session_context.auth_mode == "basic"
        assert session_context.credential_path == "config/cyclecloud-credentials.json"
        await session_context.client.aclose()

    def test_build_settings_does_not_carry_raw_credentials_in_repr(self):
        builder = CycleCloudSessionBuilder(
            cc_url="https://cc.example.com",
            verify_ssl=False,
            template=None,
            request_context=CycleCloudRequestContext.from_mapping(
                {"cyclecloud_credential_path": "/tmp/cyclecloud.json"}
            ),
            provider_cfg=None,
        )
        with patch.object(
            CycleCloudSessionBuilder,
            "_load_credential_file",
            return_value=CycleCloudCredentialData(
                username="cc_admin",
                password="changeme",
            ),
        ):
            settings = builder.build_settings()

        settings_repr = repr(settings)
        assert "cc_admin" not in settings_repr
        assert "changeme" not in settings_repr
