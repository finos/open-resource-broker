"""Tests for the CycleCloud handler and related template/exception additions."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import CredentialUnavailableError

from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.exceptions.azure_exceptions import (
    AuthenticationError,
    CycleCloudConnectionError,
    CycleCloudNodeError,
    TerminationError,
)
from orb.providers.azure.infrastructure.handlers.cyclecloud_handler import (
    CycleCloudHandler,
    resolve_cc_state,
)
from orb.providers.azure.infrastructure.cyclecloud_session import (
    CycleCloudCredentialData,
    CycleCloudRequestContext,
)
from orb.providers.azure.infrastructure.cyclecloud_session_builder import (
    CycleCloudSessionBuilder,
)


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
    fields = {**_CC_TEMPLATE_FIELDS, **overrides}
    return AzureTemplate(**fields)


def _make_handler():
    azure_client = MagicMock()
    azure_client.get_provider_config.return_value = None
    logger = MagicMock()
    return CycleCloudHandler(azure_client=azure_client, logger=logger)


def _make_request(count=2, resource_ids=None, metadata=None):
    req = MagicMock()
    req.request_id = "req-12345678-1234-1234-1234-123456789012"
    req.requested_count = count
    req.resource_ids = resource_ids or []
    req.metadata = metadata or {}
    return req


def _make_cc_builder(*, handler, credential=None, request_context=None):
    token_provider = None
    if credential is not None:
        token_provider = MagicMock()
        token_provider.get_access_token.side_effect = lambda scope: credential.get_token(scope).token
        token_provider.get_auth_error_types.return_value = (
            CredentialUnavailableError,
            ClientAuthenticationError,
        )
    return CycleCloudSessionBuilder(
        cc_url="https://cc.example.com",
        verify_ssl=True,
        template=None,
        request_context=request_context or CycleCloudRequestContext(),
        provider_cfg=handler.azure_client.get_provider_config(),
        token_provider=token_provider,
    )


def _make_cc_request_context(**values):
    return CycleCloudRequestContext.from_mapping(values)


# ---------------------------------------------------------------------------
# AzureTemplate CycleCloud fields
# ---------------------------------------------------------------------------


class TestCycleCloudTemplate:
    def test_cyclecloud_template_construction(self):
        t = _make_template()
        assert t.provider_api.value == "CycleCloud"
        assert t.cluster_name == "my-cluster"
        assert t.node_array == "execute"
        assert t.cyclecloud_url == "https://cc.example.com"
        assert t.cyclecloud_credential_path is None
        assert t.cyclecloud_auth_mode == "bearer"
        assert t.cyclecloud_aad_scope == "https://cc.example.com/.default"
        assert t.cyclecloud_verify_ssl is False

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

    def test_cyclecloud_template_no_ssh_required(self):
        """CycleCloud manages SSH internally — no ssh_key_name or ssh_public_keys needed."""
        t = _make_template()
        assert t.ssh_public_keys == []
        assert t.ssh_key_name is None

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
    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_acquire_hosts_success(self, mock_session_cls):
        handler = _make_handler()
        template = _make_template()
        request = _make_request(count=2)

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        # Mock responses: cluster status, then node create
        cluster_status_resp = MagicMock()
        cluster_status_resp.status_code = 200
        cluster_status_resp.content = b'{"state": "Started"}'
        cluster_status_resp.json.return_value = {"state": "Started"}
        cluster_status_resp.raise_for_status = MagicMock()

        node_create_resp = MagicMock()
        node_create_resp.status_code = 200
        node_create_resp.headers = {"Location": "https://cc.example.com/operations/op-123"}
        node_create_resp.content = b'{"operationId": "op-123", "sets": [{"added": 2, "nodes": [{"name": "node-1", "status": "Acquiring"}, {"name": "node-2", "status": "Acquiring"}]}]}'
        node_create_resp.json.return_value = {
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
        }
        node_create_resp.raise_for_status = MagicMock()

        mock_session.request.side_effect = [cluster_status_resp, node_create_resp]

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert result["resource_ids"] == ["req-12345678-1234-1234-1234-123456789012"]
        assert result["instances"] == []
        assert result["provider_data"]["cluster_name"] == "my-cluster"
        assert result["provider_data"]["operation_id"] == "op-123"
        assert result["provider_data"]["operation_location"] == "https://cc.example.com/operations/op-123"
        assert result["provider_data"]["added_count"] == 2
        assert result["provider_data"]["submitted_count"] == 2
        assert result["provider_data"]["operation_status"] == "submitted"
        assert result["provider_data"]["fulfillment_final"] is True
        request_json = mock_session.request.call_args_list[1].kwargs["json"]
        assert request_json["requestId"] == "req-12345678-1234-1234-1234-123456789012"
        mock_session.close.assert_called_once_with()

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_acquire_hosts_placeholder_nodes(self, mock_session_cls):
        """When CycleCloud returns added count but no inline node details, tracking stays resource-level."""
        handler = _make_handler()
        template = _make_template()
        request = _make_request(count=3)

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        cluster_resp = MagicMock()
        cluster_resp.content = b'{"state": "Started"}'
        cluster_resp.json.return_value = {"state": "Started"}
        cluster_resp.raise_for_status = MagicMock()

        create_resp = MagicMock()
        create_resp.content = b'{"operationId": "op-456", "sets": [{"added": 3, "nodes": []}]}'
        create_resp.json.return_value = {
            "operationId": "op-456",
            "sets": [{"added": 3, "nodes": []}],
        }
        create_resp.raise_for_status = MagicMock()

        mock_session.request.side_effect = [cluster_resp, create_resp]

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert result["instances"] == []
        assert result["provider_data"]["added_count"] == 3

    def test_acquire_hosts_missing_cluster_name(self):
        """Should raise CycleCloudNodeError if cluster_name is missing."""
        handler = _make_handler()
        # Bypass template validation by setting cluster_name to a truthy value
        # then overriding via object.__setattr__
        template = _make_template()
        object.__setattr__(template, "cluster_name", None)
        request = _make_request()

        with pytest.raises(CycleCloudNodeError, match="cluster_name is required"):
            handler.acquire_hosts(request, template)

    def test_acquire_hosts_missing_url(self):
        """Should raise CycleCloudConnectionError if cyclecloud_url is missing."""
        handler = _make_handler()
        template = _make_template()
        object.__setattr__(template, "cyclecloud_url", None)
        request = _make_request()

        with pytest.raises(CycleCloudConnectionError, match="cyclecloud_url is required"):
            handler.acquire_hosts(request, template)

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_acquire_hosts_collects_failed_node_errors(self, mock_session_cls):
        handler = _make_handler()
        template = _make_template()
        request = _make_request(count=1)

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        cluster_resp = MagicMock()
        cluster_resp.content = b'{"state": "Started"}'
        cluster_resp.json.return_value = {"state": "Started"}
        cluster_resp.raise_for_status = MagicMock()

        create_resp = MagicMock()
        create_resp.content = (
            b'{"operationId": "op-999", "sets": [{"added": 1, "nodes": '
            b'[{"name": "node-err", "status": "Failed", "Message": "Quota exhausted"}]}]}'
        )
        create_resp.json.return_value = {
            "operationId": "op-999",
            "sets": [
                {
                    "added": 1,
                    "nodes": [
                        {"name": "node-err", "status": "Failed", "Message": "Quota exhausted"}
                    ],
                }
            ],
        }
        create_resp.raise_for_status = MagicMock()

        mock_session.request.side_effect = [cluster_resp, create_resp]

        result = handler.acquire_hosts(request, template)

        assert result["instances"] == []
        assert result["provider_data"]["fleet_errors"][0]["error_message"] == "Quota exhausted"
        assert result["provider_data"]["fleet_errors"][0]["error_code"] == "NodeFailed"


# ---------------------------------------------------------------------------
# CycleCloudHandler - check_hosts_status
# ---------------------------------------------------------------------------


class TestCycleCloudHandlerStatus:
    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_check_hosts_status_success(self, mock_session_cls):
        handler = _make_handler()

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        nodes_resp = MagicMock()
        nodes_resp.content = b'{"nodes": []}'
        nodes_resp.json.return_value = {
            "nodes": [
                {
                    "Name": "node-1",
                    "NodeId": "id-1",
                    "NodeArray": "execute",
                    "State": "Ready",
                    "PrivateIp": "10.0.0.1",
                    "MachineType": "Standard_D4s_v5",
                },
                {
                    "Name": "node-2",
                    "NodeId": "id-2",
                    "NodeArray": "execute",
                    "State": "Preparing",
                    "PrivateIp": "10.0.0.2",
                    "MachineType": "Standard_D4s_v5",
                },
            ]
        }
        nodes_resp.raise_for_status = MagicMock()
        mock_session.request.return_value = nodes_resp

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

        results = handler.check_hosts_status(request)

        assert len(results) == 2
        assert results[0]["instance_id"] == "id-1"
        assert results[0]["name"] == "node-1"
        assert results[0]["resource_id"] == "my-cluster"
        assert results[0]["status"] == "running"
        assert results[0]["private_ip"] == "10.0.0.1"
        assert results[1]["status"] == "pending"

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_check_hosts_status_uses_request_id_filter(self, mock_session_cls):
        handler = _make_handler()
        handler.azure_client.get_provider_config.return_value = AzureProviderConfig(
            region="eastus2",
            connect_timeout=7,
            read_timeout=11,
        )

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        nodes_resp = MagicMock()
        nodes_resp.content = b'{"nodes": [{"Name": "node-1", "NodeId": "id-1", "NodeArray": "execute", "State": "Ready"}]}'
        nodes_resp.json.return_value = {
            "nodes": [
                {
                    "Name": "node-1",
                    "NodeId": "id-1",
                    "NodeArray": "execute",
                    "State": "Ready",
                }
            ]
        }
        nodes_resp.raise_for_status = MagicMock()
        mock_session.request.return_value = nodes_resp

        request = _make_request(
            resource_ids=["req-12345678-1234-1234-1234-123456789012"],
            metadata={
                "cluster_name": "my-cluster",
                "node_array": "execute",
                "operation_id": "op-123",
                "operation_location": "https://cc.example.com/operations/op-123",
                "cyclecloud_url": "https://cc.example.com",
                "cyclecloud_auth_mode": "bearer",
                "cyclecloud_aad_scope": "https://cc.example.com/.default",
            },
        )

        results = handler.check_hosts_status(request)

        assert len(results) == 1
        assert results[0]["instance_id"] == "id-1"
        mock_session.request.assert_called_once_with(
            "GET",
            "https://cc.example.com/clusters/my-cluster/nodes",
            params={"request_id": "req-12345678-1234-1234-1234-123456789012"},
            timeout=(7, 11),
        )
        mock_session.close.assert_called_once_with()

    def test_check_hosts_status_no_resource_ids(self):
        handler = _make_handler()
        request = _make_request(resource_ids=[])
        assert handler.check_hosts_status(request) == []

    def test_check_hosts_status_no_cc_url(self):
        handler = _make_handler()
        request = _make_request(
            resource_ids=["req-12345678-1234-1234-1234-123456789012"],
            metadata={"cluster_name": "my-cluster"},
        )
        with pytest.raises(CycleCloudConnectionError, match="cyclecloud_url is required"):
            handler.check_hosts_status(request)

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
            handler.check_hosts_status(request)

    def test_check_hosts_status_requires_cluster_name(self):
        handler = _make_handler()
        request = _make_request(
            resource_ids=["req-12345678-1234-1234-1234-123456789012"],
            metadata={"cyclecloud_url": "https://cc.example.com"},
        )

        with pytest.raises(CycleCloudConnectionError, match="cluster_name is required"):
            handler.check_hosts_status(request)

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_check_hosts_status_request_failure_raises(self, mock_session_cls):
        handler = _make_handler()

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.request.side_effect = requests.exceptions.ConnectionError("boom")

        request = _make_request(
            resource_ids=["req-12345678-1234-1234-1234-123456789012"],
            metadata={
                "cluster_name": "my-cluster",
                "cyclecloud_url": "https://cc.example.com",
            },
        )

        with pytest.raises(CycleCloudConnectionError, match="Cannot connect to CycleCloud"):
            handler.check_hosts_status(request)

        assert handler._logger.error.call_count == 1
        assert handler._logger.error.call_args.args[0] == (
            "Failed to build CycleCloud session for status check (cluster '%s'): %s"
        )
        assert handler._logger.error.call_args.args[1] == "my-cluster"

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_check_hosts_status_filters_by_node_array(self, mock_session_cls):
        handler = _make_handler()

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        nodes_resp = MagicMock()
        nodes_resp.content = b'{"nodes": []}'
        nodes_resp.json.return_value = {
            "nodes": [
                {"Name": "node-1", "NodeArray": "execute", "State": "Ready"},
                {"Name": "node-2", "NodeArray": "hpc", "State": "Ready"},
            ]
        }
        nodes_resp.raise_for_status = MagicMock()
        mock_session.request.return_value = nodes_resp

        request = _make_request(
            resource_ids=["req-12345678-1234-1234-1234-123456789012"],
            metadata={
                "cluster_name": "my-cluster",
                "node_array": "execute",
                "cyclecloud_url": "https://cc.example.com",
            },
        )

        results = handler.check_hosts_status(request)
        assert len(results) == 1
        assert results[0]["instance_id"] == "node-1"

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_check_hosts_status_accepts_lowercase_node_fields(self, mock_session_cls):
        handler = _make_handler()

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        nodes_resp = MagicMock()
        nodes_resp.content = b'{"nodes": []}'
        nodes_resp.json.return_value = {
            "nodes": [
                {
                    "name": "node-1",
                    "nodeId": "id-1",
                    "nodearray": "execute",
                    "state": "Ready",
                    "privateIp": "10.0.0.1",
                    "machineType": "Standard_D4s_v5",
                }
            ]
        }
        nodes_resp.raise_for_status = MagicMock()
        mock_session.request.return_value = nodes_resp

        request = _make_request(
            resource_ids=["req-12345678-1234-1234-1234-123456789012"],
            metadata={
                "cluster_name": "my-cluster",
                "node_array": "execute",
                "cyclecloud_url": "https://cc.example.com",
            },
        )

        results = handler.check_hosts_status(request)

        assert len(results) == 1
        assert results[0]["instance_id"] == "id-1"
        assert results[0]["name"] == "node-1"
        assert results[0]["status"] == "running"
        assert results[0]["private_ip"] == "10.0.0.1"
        assert results[0]["provider_data"]["node_id"] == "id-1"

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_check_hosts_status_accepts_template_and_status_fields(self, mock_session_cls):
        handler = _make_handler()

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        nodes_resp = MagicMock()
        nodes_resp.content = b'{"nodes": []}'
        nodes_resp.json.return_value = {
            "nodes": [
                {
                    "Name": "dynamic-1",
                    "NodeId": "id-1",
                    "Template": "dynamic",
                    "Status": "Ready",
                    "State": "Started",
                    "IpAddress": "10.0.1.5",
                    "MachineType": "Standard_F2s_v2",
                }
            ]
        }
        nodes_resp.raise_for_status = MagicMock()
        mock_session.request.return_value = nodes_resp

        request = _make_request(
            resource_ids=["req-12345678-1234-1234-1234-123456789012"],
            metadata={
                "cluster_name": "my-cluster",
                "node_array": "dynamic",
                "cyclecloud_url": "https://cc.example.com",
            },
        )

        results = handler.check_hosts_status(request)

        assert len(results) == 1
        assert results[0]["instance_id"] == "id-1"
        assert results[0]["name"] == "dynamic-1"
        assert results[0]["status"] == "running"
        assert results[0]["private_ip"] == "10.0.1.5"
        assert results[0]["provider_data"]["node_array"] == "dynamic"

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_check_hosts_status_includes_failed_node_errors(self, mock_session_cls):
        handler = _make_handler()

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        nodes_resp = MagicMock()
        nodes_resp.content = b'{"nodes": []}'
        nodes_resp.json.return_value = {
            "nodes": [
                {
                    "Name": "node-fail",
                    "NodeId": "id-fail",
                    "NodeArray": "execute",
                    "State": "Failed",
                    "Message": "Node startup failed",
                    "MachineType": "Standard_D4s_v5",
                }
            ]
        }
        nodes_resp.raise_for_status = MagicMock()
        mock_session.request.return_value = nodes_resp

        request = _make_request(
            resource_ids=["req-12345678-1234-1234-1234-123456789012"],
            metadata={
                "cluster_name": "my-cluster",
                "node_array": "execute",
                "cyclecloud_url": "https://cc.example.com",
            },
        )

        results = handler.check_hosts_status(request)

        assert results[0]["status"] == "failed"
        assert results[0]["provider_data"]["fleet_errors"][0]["error_code"] == "NodeFailed"
        assert results[0]["provider_data"]["fleet_errors"][0]["error_message"] == "Node startup failed"


# ---------------------------------------------------------------------------
# CycleCloudHandler - release_hosts
# ---------------------------------------------------------------------------


class TestCycleCloudHandlerRelease:
    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_release_hosts_success(self, mock_session_cls):
        handler = _make_handler()

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        nodes_resp = MagicMock()
        nodes_resp.content = b'{"nodes": [{"Name": "node-1", "NodeId": "node-1"}, {"Name": "node-2", "NodeId": "node-2"}]}'
        nodes_resp.json.return_value = {
            "nodes": [
                {"Name": "node-1", "NodeId": "node-1"},
                {"Name": "node-2", "NodeId": "node-2"},
            ]
        }
        nodes_resp.raise_for_status = MagicMock()

        empty_resp = MagicMock()
        empty_resp.content = b""
        empty_resp.raise_for_status = MagicMock()
        mock_session.request.side_effect = [nodes_resp, empty_resp, empty_resp]

        handler.release_hosts(
            machine_ids=["node-1", "node-2"],
            resource_id="my-cluster",
            context={
                "cyclecloud_url": "https://cc.example.com",
                "cyclecloud_auth_mode": "bearer",
                "cyclecloud_aad_scope": "https://cc.example.com/.default",
            },
        )

        # Verify lookup + terminate call were made
        assert mock_session.request.call_count == 2
        calls = mock_session.request.call_args_list
        assert "nodes" in calls[0].args[1]
        assert "terminate" in calls[1].args[1]
        assert calls[1].kwargs["json"] == {"ids": ["node-1", "node-2"]}
        mock_session.close.assert_called_once_with()

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_release_hosts_resolves_node_id_to_node_name(self, mock_session_cls):
        handler = _make_handler()

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        nodes_resp = MagicMock()
        nodes_resp.content = b'{"nodes": [{"Name": "dynamic-1", "NodeId": "cluster-dynamic-op-0"}]}'
        nodes_resp.json.return_value = {
            "nodes": [
                {"Name": "dynamic-1", "NodeId": "cluster-dynamic-op-0"},
            ]
        }
        nodes_resp.raise_for_status = MagicMock()

        empty_resp = MagicMock()
        empty_resp.content = b""
        empty_resp.raise_for_status = MagicMock()

        mock_session.request.side_effect = [nodes_resp, empty_resp]

        handler.release_hosts(
            machine_ids=["cluster-dynamic-op-0"],
            resource_id="my-cluster",
            context={
                "cyclecloud_url": "https://cc.example.com",
                "cyclecloud_auth_mode": "bearer",
                "cyclecloud_aad_scope": "https://cc.example.com/.default",
            },
        )

        assert mock_session.request.call_count == 2
        calls = mock_session.request.call_args_list
        assert calls[0].args[0] == "GET"
        assert calls[1].kwargs["json"] == {"ids": ["cluster-dynamic-op-0"]}

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_release_hosts_resolves_lowercase_node_id_to_node_name(self, mock_session_cls):
        handler = _make_handler()

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        nodes_resp = MagicMock()
        nodes_resp.content = b'{"nodes": [{"name": "dynamic-1", "nodeId": "cluster-dynamic-op-0"}]}'
        nodes_resp.json.return_value = {
            "nodes": [
                {"name": "dynamic-1", "nodeId": "cluster-dynamic-op-0"},
            ]
        }
        nodes_resp.raise_for_status = MagicMock()

        empty_resp = MagicMock()
        empty_resp.content = b""
        empty_resp.raise_for_status = MagicMock()

        mock_session.request.side_effect = [nodes_resp, empty_resp]

        handler.release_hosts(
            machine_ids=["cluster-dynamic-op-0"],
            resource_id="my-cluster",
            context={
                "cyclecloud_url": "https://cc.example.com",
                "cyclecloud_auth_mode": "bearer",
                "cyclecloud_aad_scope": "https://cc.example.com/.default",
            },
        )

        calls = mock_session.request.call_args_list
        assert calls[1].kwargs["json"] == {"ids": ["cluster-dynamic-op-0"]}

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_release_hosts_prefers_context_cluster_name(self, mock_session_cls):
        handler = _make_handler()

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        nodes_resp = MagicMock()
        nodes_resp.content = b'{"nodes": [{"Name": "dynamic-1", "NodeId": "cluster-dynamic-op-0"}]}'
        nodes_resp.json.return_value = {
            "nodes": [{"Name": "dynamic-1", "NodeId": "cluster-dynamic-op-0"}]
        }
        nodes_resp.raise_for_status = MagicMock()

        empty_resp = MagicMock()
        empty_resp.content = b""
        empty_resp.raise_for_status = MagicMock()

        mock_session.request.side_effect = [nodes_resp, empty_resp]

        handler.release_hosts(
            machine_ids=["cluster-dynamic-op-0"],
            resource_id="req-legacy-resource-id",
            context={
                "cluster_name": "my-cluster",
                "cyclecloud_url": "https://cc.example.com",
                "cyclecloud_auth_mode": "bearer",
                "cyclecloud_aad_scope": "https://cc.example.com/.default",
            },
        )

        calls = mock_session.request.call_args_list
        assert calls[0].args[1] == "https://cc.example.com/clusters/my-cluster/nodes"
        assert calls[1].args[1] == "https://cc.example.com/clusters/my-cluster/nodes/terminate"

    def test_release_hosts_missing_url(self):
        handler = _make_handler()
        with pytest.raises(TerminationError, match="cyclecloud_url is required"):
            handler.release_hosts(
                machine_ids=["node-1"],
                resource_id="my-cluster",
                context={},
            )


# ---------------------------------------------------------------------------
# CycleCloudHandler - auth modes
# ---------------------------------------------------------------------------


class TestCycleCloudAuthModes:
    def test_cc_request_uses_provider_configured_timeouts(self):
        handler = _make_handler()
        handler.azure_client.get_provider_config.return_value = AzureProviderConfig(
            region="eastus2",
            connect_timeout=5,
            read_timeout=13,
        )
        session = MagicMock()
        response = MagicMock()
        response.content = b"{}"
        response.json.return_value = {}
        response.raise_for_status = MagicMock()
        session.request.return_value = response

        handler._cc_request(
            session,
            "GET",
            "https://cc.example.com/clusters/my-cluster/status",
        )

        session.request.assert_called_once_with(
            "GET",
            "https://cc.example.com/clusters/my-cluster/status",
            timeout=(5, 13),
        )

    def test_build_session_uses_azure_bearer_when_no_basic_auth(self):
        handler = _make_handler()

        with patch.object(
            CycleCloudSessionBuilder,
            "_get_azure_bearer_token",
            return_value="tok-123",
        ):
            session_context = handler._build_cc_session(
                cc_url="https://cc.example.com",
                verify_ssl=True,
                request_context=_make_cc_request_context(
                    cyclecloud_auth_mode="bearer"
                ),
            )

        assert session_context.base_url == "https://cc.example.com"
        assert session_context.auth_mode == "bearer"
        assert session_context.session.headers["Authorization"] == "Bearer tok-123"

    def test_build_session_rejects_ssh_auth_mode(self):
        handler = _make_handler()

        with pytest.raises(
            CycleCloudConnectionError,
            match="cyclecloud_auth_mode=ssh is not supported",
        ):
            handler._build_cc_session(
                cc_url="https://cc.example.com",
                verify_ssl=True,
                request_context=_make_cc_request_context(cyclecloud_auth_mode="ssh"),
            )

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_build_session_closes_session_when_auth_resolution_fails(self, mock_session_cls):
        handler = _make_handler()
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        with patch.object(
            CycleCloudSessionBuilder,
            "_get_azure_bearer_token",
            return_value=None,
        ):
            with pytest.raises(
                CycleCloudConnectionError,
                match="cyclecloud_auth_mode=bearer requested but no bearer token could be resolved",
            ):
                handler._build_cc_session(
                    cc_url="https://cc.example.com",
                    verify_ssl=True,
                    request_context=_make_cc_request_context(
                        cyclecloud_auth_mode="bearer"
                    ),
                )

        mock_session.close.assert_called_once_with()

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_build_session_closes_session_when_transport_setup_fails(self, mock_session_cls):
        handler = _make_handler()
        mock_session = MagicMock()
        mock_session.headers.update.side_effect = RuntimeError("header setup failed")
        mock_session_cls.return_value = mock_session

        with pytest.raises(RuntimeError, match="header setup failed"):
            handler._build_cc_session(
                cc_url="https://cc.example.com",
                verify_ssl=True,
                request_context=_make_cc_request_context(
                    cyclecloud_auth_mode="bearer"
                ),
            )

        mock_session.close.assert_called_once_with()

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_cc_session_scope_builds_session_on_enter(self, mock_session_cls):
        handler = _make_handler()
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        with patch.object(
            CycleCloudSessionBuilder,
            "_get_azure_bearer_token",
            return_value="tok-123",
        ):
            scope = handler._cc_session_scope(
                cc_url="https://cc.example.com",
                verify_ssl=True,
                request_context=_make_cc_request_context(
                    cyclecloud_auth_mode="bearer"
                ),
            )

            mock_session_cls.assert_not_called()

            with scope as session_context:
                assert session_context.base_url == "https://cc.example.com"

        mock_session_cls.assert_called_once_with()
        mock_session.close.assert_called_once_with()

    def test_build_session_loads_cyclecloud_config_from_provider(self):
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
            session_context = handler._build_cc_session(
                cc_url=None,
                verify_ssl=None,
            )

        assert session_context.base_url == "https://cc.example.com"
        assert session_context.session.verify is False
        assert session_context.auth_mode == "basic"
        assert session_context.session.auth == ("cc_admin", "changeme")

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

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_acquire_hosts_persists_credential_path(self, mock_session_cls, tmp_path: Path):
        handler = _make_handler()
        credential_file = tmp_path / "cyclecloud-credentials.json"
        credential_file.write_text(
            json.dumps({"username": "file-admin", "password": "file-secret"}),
            encoding="utf-8",
        )
        template = _make_template(
            cyclecloud_auth_mode=None,
            cyclecloud_credential_path=str(credential_file),
        )
        request = _make_request(count=1)

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        cluster_status_resp = MagicMock()
        cluster_status_resp.status_code = 200
        cluster_status_resp.content = b'{"state": "Started"}'
        cluster_status_resp.json.return_value = {"state": "Started"}
        cluster_status_resp.raise_for_status = MagicMock()

        node_create_resp = MagicMock()
        node_create_resp.status_code = 200
        node_create_resp.content = b'{"operationId": "op-123", "sets": [{"added": 1, "nodes": [{"name": "node-1", "status": "Acquiring"}]}]}'
        node_create_resp.json.return_value = {
            "operationId": "op-123",
            "sets": [{"added": 1, "nodes": [{"name": "node-1", "status": "Acquiring"}]}],
        }
        node_create_resp.raise_for_status = MagicMock()

        mock_session.request.side_effect = [cluster_status_resp, node_create_resp]

        result = handler.acquire_hosts(request, template)

        assert result["provider_data"]["cyclecloud_credential_path"] == str(credential_file)

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_acquire_hosts_persists_resolved_provider_config_credential_path(
        self,
        mock_session_cls,
        tmp_path: Path,
    ):
        handler = _make_handler()
        credential_file = tmp_path / "cyclecloud-provider-credentials.json"
        credential_file.write_text(
            json.dumps(
                {
                    "username": "file-admin",
                    "password": "file-secret",
                    "url": "https://cc.example.com",
                }
            ),
            encoding="utf-8",
        )
        handler.azure_client.get_provider_config.return_value = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            region="eastus2",
            cyclecloud={
                "credential_path": str(credential_file),
                "url": "https://cc.example.com",
                "verify_ssl": False,
            },
        )
        template_fields = dict(_CC_TEMPLATE_FIELDS)
        template_fields.update({
            "cyclecloud_url": None,
            "cyclecloud_auth_mode": None,
            "cyclecloud_credential_path": None,
        })
        del template_fields["cyclecloud_verify_ssl"]
        template = AzureTemplate(**template_fields)
        request = _make_request(count=1)

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        cluster_status_resp = MagicMock()
        cluster_status_resp.status_code = 200
        cluster_status_resp.content = b'{"state": "Started"}'
        cluster_status_resp.json.return_value = {"state": "Started"}
        cluster_status_resp.raise_for_status = MagicMock()

        node_create_resp = MagicMock()
        node_create_resp.status_code = 200
        node_create_resp.content = b'{"operationId": "op-123", "sets": [{"added": 1, "nodes": [{"name": "node-1", "status": "Acquiring"}]}]}'
        node_create_resp.json.return_value = {
            "operationId": "op-123",
            "sets": [{"added": 1, "nodes": [{"name": "node-1", "status": "Acquiring"}]}],
        }
        node_create_resp.raise_for_status = MagicMock()

        mock_session.request.side_effect = [cluster_status_resp, node_create_resp]

        result = handler.acquire_hosts(request, template)

        assert result["provider_data"]["cyclecloud_credential_path"] == str(credential_file)
        assert result["provider_data"]["cyclecloud_verify_ssl"] is False
        assert mock_session.verify is False

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_acquire_hosts_explicit_template_verify_ssl_true_overrides_provider_false(
        self,
        mock_session_cls,
        tmp_path: Path,
    ):
        handler = _make_handler()
        credential_file = tmp_path / "cyclecloud-provider-credentials.json"
        credential_file.write_text(
            json.dumps(
                {
                    "username": "file-admin",
                    "password": "file-secret",
                    "url": "https://cc.example.com",
                }
            ),
            encoding="utf-8",
        )
        handler.azure_client.get_provider_config.return_value = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            region="eastus2",
            cyclecloud={
                "credential_path": str(credential_file),
                "url": "https://cc.example.com",
                "verify_ssl": False,
            },
        )
        template = _make_template(
            cyclecloud_url=None,
            cyclecloud_auth_mode=None,
            cyclecloud_credential_path=None,
            cyclecloud_verify_ssl=True,
        )
        request = _make_request(count=1)

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        cluster_status_resp = MagicMock()
        cluster_status_resp.status_code = 200
        cluster_status_resp.content = b'{"state": "Started"}'
        cluster_status_resp.json.return_value = {"state": "Started"}
        cluster_status_resp.raise_for_status = MagicMock()

        node_create_resp = MagicMock()
        node_create_resp.status_code = 200
        node_create_resp.content = b'{"operationId": "op-123", "sets": [{"added": 1, "nodes": [{"name": "node-1", "status": "Acquiring"}]}]}'
        node_create_resp.json.return_value = {
            "operationId": "op-123",
            "sets": [{"added": 1, "nodes": [{"name": "node-1", "status": "Acquiring"}]}],
        }
        node_create_resp.raise_for_status = MagicMock()

        mock_session.request.side_effect = [cluster_status_resp, node_create_resp]

        result = handler.acquire_hosts(request, template)

        assert result["provider_data"]["cyclecloud_verify_ssl"] is True
        assert mock_session.verify is True

    @patch("orb.providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_acquire_hosts_honors_explicit_template_verify_ssl_false(
        self,
        mock_session_cls,
        tmp_path: Path,
    ):
        handler = _make_handler()
        credential_file = tmp_path / "cyclecloud-provider-credentials.json"
        credential_file.write_text(
            json.dumps(
                {
                    "username": "file-admin",
                    "password": "file-secret",
                    "url": "https://cc.example.com",
                }
            ),
            encoding="utf-8",
        )
        handler.azure_client.get_provider_config.return_value = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            region="eastus2",
            cyclecloud={
                "credential_path": str(credential_file),
                "url": "https://cc.example.com",
                "verify_ssl": True,
            },
        )
        template = _make_template(
            cyclecloud_url=None,
            cyclecloud_auth_mode=None,
            cyclecloud_credential_path=None,
            cyclecloud_verify_ssl=False,
        )
        request = _make_request(count=1)

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        cluster_status_resp = MagicMock()
        cluster_status_resp.status_code = 200
        cluster_status_resp.content = b'{"state": "Started"}'
        cluster_status_resp.json.return_value = {"state": "Started"}
        cluster_status_resp.raise_for_status = MagicMock()

        node_create_resp = MagicMock()
        node_create_resp.status_code = 200
        node_create_resp.content = b'{"operationId": "op-123", "sets": [{"added": 1, "nodes": [{"name": "node-1", "status": "Acquiring"}]}]}'
        node_create_resp.json.return_value = {
            "operationId": "op-123",
            "sets": [{"added": 1, "nodes": [{"name": "node-1", "status": "Acquiring"}]}],
        }
        node_create_resp.raise_for_status = MagicMock()

        mock_session.request.side_effect = [cluster_status_resp, node_create_resp]

        result = handler.acquire_hosts(request, template)

        assert result["provider_data"]["cyclecloud_verify_ssl"] is False
        assert mock_session.verify is False
