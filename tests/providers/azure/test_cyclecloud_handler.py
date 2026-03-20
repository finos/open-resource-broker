"""Tests for the CycleCloud handler and related template/exception additions."""

import json
from pathlib import Path
import pytest
import requests
from unittest.mock import MagicMock, patch

from providers.azure.configuration.config import AzureProviderConfig
from providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from providers.azure.exceptions.azure_exceptions import (
    CycleCloudConnectionError,
    CycleCloudNodeError,
    TerminationError,
)
from providers.azure.infrastructure.handlers.cyclecloud_handler import (
    CycleCloudHandler,
    _resolve_cc_state,
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
    "cyclecloud_username": "admin",
    "cyclecloud_password": "secret",
    "cyclecloud_verify_ssl": False,
}


def _make_template(**overrides):
    fields = {**_CC_TEMPLATE_FIELDS, **overrides}
    return AzureTemplate(**fields)


def _make_handler():
    azure_client = MagicMock()
    logger = MagicMock()
    return CycleCloudHandler(azure_client=azure_client, logger=logger)


def _make_request(count=2, resource_ids=None, metadata=None):
    req = MagicMock()
    req.requested_count = count
    req.resource_ids = resource_ids or []
    req.metadata = metadata or {}
    return req


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
        assert t.cyclecloud_username == "admin"
        assert t.cyclecloud_password == "secret"
        assert t.cyclecloud_verify_ssl is False

    def test_cyclecloud_template_accepts_credential_path(self):
        t = _make_template(
            cyclecloud_username=None,
            cyclecloud_password=None,
            cyclecloud_credential_path="config/cyclecloud-credentials.json",
        )
        assert t.cyclecloud_credential_path == "config/cyclecloud-credentials.json"

    def test_cyclecloud_template_accepts_explicit_auth_fields(self):
        t = _make_template(
            cyclecloud_auth_mode="bearer",
            cyclecloud_bearer_token="token-123",
            cyclecloud_aad_scope="https://example/.default",
        )
        assert t.cyclecloud_auth_mode == "bearer"
        assert t.cyclecloud_bearer_token == "token-123"
        assert t.cyclecloud_aad_scope == "https://example/.default"

    def test_cyclecloud_template_no_ssh_required(self):
        """CycleCloud manages SSH internally — no ssh_key_name or ssh_public_keys needed."""
        t = _make_template()
        assert t.ssh_public_keys == []
        assert t.ssh_key_name is None

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
        assert _resolve_cc_state(cc_state) == expected


# ---------------------------------------------------------------------------
# CycleCloudHandler - acquire_hosts
# ---------------------------------------------------------------------------


class TestCycleCloudHandlerAcquire:
    @patch("providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
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
        assert result["resource_ids"] == ["my-cluster"]
        assert len(result["instances"]) == 2
        assert result["instances"][0]["instance_id"] == "node-1"
        assert result["instances"][0]["status"] == "pending"
        assert result["provider_data"]["cluster_name"] == "my-cluster"
        assert result["provider_data"]["operation_id"] == "op-123"

    @patch("providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_acquire_hosts_placeholder_nodes(self, mock_session_cls):
        """When CycleCloud returns added count but no inline node details."""
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
        assert len(result["instances"]) == 3
        assert all(instance["status"] == "pending" for instance in result["instances"])

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

    @patch("providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
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

        assert result["instances"][0]["status"] == "failed"
        assert result["instances"][0]["provider_data"]["fleet_errors"][0]["error_code"] == "NodeFailed"
        assert result["provider_data"]["fleet_errors"][0]["error_message"] == "Quota exhausted"


# ---------------------------------------------------------------------------
# CycleCloudHandler - check_hosts_status
# ---------------------------------------------------------------------------


class TestCycleCloudHandlerStatus:
    @patch("providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
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
            resource_ids=["my-cluster"],
            metadata={
                "cluster_name": "my-cluster",
                "node_array": "execute",
                "cyclecloud_url": "https://cc.example.com",
                "cyclecloud_username": "admin",
                "cyclecloud_password": "secret",
            },
        )

        results = handler.check_hosts_status(request)

        assert len(results) == 2
        assert results[0]["instance_id"] == "node-1"
        assert results[0]["status"] == "running"
        assert results[0]["private_ip"] == "10.0.0.1"
        assert results[1]["status"] == "pending"

    def test_check_hosts_status_no_resource_ids(self):
        handler = _make_handler()
        request = _make_request(resource_ids=[])
        assert handler.check_hosts_status(request) == []

    def test_check_hosts_status_no_cc_url(self):
        handler = _make_handler()
        request = _make_request(
            resource_ids=["my-cluster"],
            metadata={"cluster_name": "my-cluster"},
        )
        with pytest.raises(CycleCloudConnectionError, match="cyclecloud_url is required"):
            handler.check_hosts_status(request)

    @patch("providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_check_hosts_status_request_failure_raises(self, mock_session_cls):
        handler = _make_handler()

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.request.side_effect = requests.exceptions.ConnectionError("boom")

        request = _make_request(
            resource_ids=["my-cluster"],
            metadata={
                "cluster_name": "my-cluster",
                "cyclecloud_url": "https://cc.example.com",
            },
        )

        with pytest.raises(CycleCloudConnectionError, match="Cannot connect to CycleCloud"):
            handler.check_hosts_status(request)

    @patch("providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
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
            resource_ids=["my-cluster"],
            metadata={
                "cluster_name": "my-cluster",
                "node_array": "execute",
                "cyclecloud_url": "https://cc.example.com",
            },
        )

        results = handler.check_hosts_status(request)
        assert len(results) == 1
        assert results[0]["instance_id"] == "node-1"

    @patch("providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
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
            resource_ids=["my-cluster"],
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
    @patch("providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
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
                "cyclecloud_username": "admin",
                "cyclecloud_password": "secret",
            },
        )

        # Verify lookup + deallocate + remove calls were made
        assert mock_session.request.call_count == 3
        calls = mock_session.request.call_args_list
        assert "nodes" in calls[0].args[1]
        assert "deallocate" in calls[1].args[1]
        assert "remove" in calls[2].args[1]

    @patch("providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
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

        mock_session.request.side_effect = [nodes_resp, empty_resp, empty_resp]

        handler.release_hosts(
            machine_ids=["cluster-dynamic-op-0"],
            resource_id="my-cluster",
            context={
                "cyclecloud_url": "https://cc.example.com",
                "cyclecloud_username": "admin",
                "cyclecloud_password": "secret",
            },
        )

        assert mock_session.request.call_count == 3
        calls = mock_session.request.call_args_list
        assert calls[0].args[0] == "GET"
        assert calls[1].kwargs["json"] == {"names": ["dynamic-1"]}
        assert calls[2].kwargs["json"] == {"names": ["dynamic-1"]}

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
    def test_build_session_uses_azure_bearer_when_no_basic_auth(self):
        handler = _make_handler()

        with patch.object(handler, "_get_azure_bearer_token", return_value="tok-123"):
            session, base_url = handler._build_cc_session(
                cc_url="https://cc.example.com",
                cc_user=None,
                cc_pass=None,
                verify_ssl=True,
                metadata={"cyclecloud_auth_mode": "bearer"},
            )

        assert base_url == "https://cc.example.com"
        assert session.__dict__.get("_cyclecloud_auth_mode") == "bearer"
        assert session.headers["Authorization"] == "Bearer tok-123"

    def test_build_session_rejects_ssh_auth_mode(self):
        handler = _make_handler()

        with pytest.raises(
            CycleCloudConnectionError,
            match="cyclecloud_auth_mode=ssh is not supported",
        ):
            handler._build_cc_session(
                cc_url="https://cc.example.com",
                cc_user=None,
                cc_pass=None,
                verify_ssl=True,
                metadata={"cyclecloud_auth_mode": "ssh"},
            )

    def test_build_session_loads_cyclecloud_config_from_provider(self):
        handler = _make_handler()
        handler.azure_client._azure_config = None
        handler.azure_client._get_selected_azure_provider_config.return_value = AzureProviderConfig(
            region="eastus2",
            resource_group="orb-test-rg",
            cyclecloud={
                "url": "https://cc.example.com",
                "username": "cc_admin",
                "password": "changeme",
                "verify_ssl": False,
            },
        )

        session, base_url = handler._build_cc_session(
            cc_url=None,
            cc_user=None,
            cc_pass=None,
            verify_ssl=None,
        )

        assert base_url == "https://cc.example.com"
        assert session.verify is False
        assert session.__dict__.get("_cyclecloud_auth_mode") == "basic"
        assert session.auth == ("cc_admin", "changeme")

    def test_build_session_loads_credentials_from_file(self, tmp_path: Path):
        handler = _make_handler()
        credential_file = tmp_path / "cyclecloud-credentials.json"
        credential_file.write_text(
            json.dumps(
                {
                    "username": "file-admin",
                    "password": "file-secret",
                    "auth_mode": "basic",
                }
            ),
            encoding="utf-8",
        )

        session, base_url = handler._build_cc_session(
            cc_url="https://cc.example.com",
            cc_user=None,
            cc_pass=None,
            verify_ssl=False,
            metadata={"cyclecloud_credential_path": str(credential_file)},
        )

        assert base_url == "https://cc.example.com"
        assert session.auth == ("file-admin", "file-secret")

    def test_build_session_parses_verify_ssl_string_from_metadata(self):
        handler = _make_handler()

        session, _ = handler._build_cc_session(
            cc_url="https://cc.example.com",
            cc_user="file-admin",
            cc_pass="file-secret",
            verify_ssl=None,
            metadata={"cyclecloud_verify_ssl": "false"},
        )

        assert session.verify is False

    def test_build_session_parses_verify_ssl_string_from_credential_file(self, tmp_path: Path):
        handler = _make_handler()
        credential_file = tmp_path / "cyclecloud-credentials.json"
        credential_file.write_text(
            json.dumps(
                {
                    "url": "https://cc.example.com",
                    "username": "file-admin",
                    "password": "file-secret",
                    "verify_ssl": "false",
                }
            ),
            encoding="utf-8",
        )

        session, base_url = handler._build_cc_session(
            cc_url=None,
            cc_user=None,
            cc_pass=None,
            verify_ssl=None,
            metadata={"cyclecloud_credential_path": str(credential_file)},
        )

        assert base_url == "https://cc.example.com"
        assert session.verify is False

    def test_build_session_resolves_credential_path_from_template(self, tmp_path: Path):
        handler = _make_handler()
        credential_file = tmp_path / "cyclecloud-credentials.json"
        credential_payload = {
            "url": "https://cc-from-template.example.com",
            "username": "template-user",
            "password": "template-pass",
            "verify_ssl": False,
            "auth_mode": "basic",
        }
        credential_file.write_text(json.dumps(credential_payload), encoding="utf-8")
        template = _make_template(
            cyclecloud_url=None,
            cyclecloud_username=None,
            cyclecloud_password=None,
            cyclecloud_credential_path=str(credential_file),
        )

        session, base_url = handler._build_cc_session(
            cc_url=None,
            cc_user=None,
            cc_pass=None,
            verify_ssl=None,
            template=template,
        )

        assert base_url == credential_payload["url"]
        assert session.auth == (credential_payload["username"], credential_payload["password"])
        assert session.verify is credential_payload["verify_ssl"]
        assert session.__dict__.get("_cyclecloud_auth_mode") == credential_payload["auth_mode"]

    @patch("providers.azure.infrastructure.handlers.cyclecloud_handler.requests.Session")
    def test_acquire_hosts_persists_credential_path(self, mock_session_cls, tmp_path: Path):
        handler = _make_handler()
        credential_file = tmp_path / "cyclecloud-credentials.json"
        credential_file.write_text(
            json.dumps({"username": "file-admin", "password": "file-secret"}),
            encoding="utf-8",
        )
        template = _make_template(
            cyclecloud_username=None,
            cyclecloud_password=None,
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
