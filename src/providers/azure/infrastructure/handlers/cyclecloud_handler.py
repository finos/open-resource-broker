"""CycleCloud Handler - provisions nodes via the Azure CycleCloud REST API.

This handler is used when ``provider_api == "CycleCloud"`` in the template.
It manages nodes within an existing CycleCloud cluster, adding nodes to a
specified node array and tracking them through the CycleCloud lifecycle.

CycleCloud REST API reference:
    https://learn.microsoft.com/en-us/azure/cyclecloud/api

Key CycleCloud concepts:
- Clusters contain one or more node arrays (partitions)
- Each node array has a VM type, image, and autoscale configuration
- Nodes are added/removed by adjusting the target count or via direct API calls
- Node states: Off → Acquiring → Preparing → Ready → (Deallocating → Deallocated)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from urllib.parse import urlparse

import requests
from pydantic import BaseModel

from domain.base.dependency_injection import injectable
from domain.request.aggregate import Request
from providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from providers.azure.domain.template.value_objects import AzureProviderApi
from providers.azure.exceptions.azure_exceptions import (
    CycleCloudClusterNotFoundError,
    CycleCloudConnectionError,
    CycleCloudNodeError,
    TerminationError,
)
from providers.azure.infrastructure.cyclecloud_session import (
    CycleCloudSessionContext,
)
from providers.azure.infrastructure.handlers.azure_handler import AzureHandler


# CycleCloud node state → domain status mapping
_CC_STATE_MAP: dict[str, str] = {
    "Off": "stopped",
    "Acquiring": "pending",
    "Preparing": "pending",
    "Starting": "pending",
    "Software Configuration": "pending",
    "Ready": "running",
    "Deallocating": "shutting-down",
    "Deallocated": "stopped",
    "Terminated": "terminated",
    "Failed": "failed",
}


def _resolve_cc_state(state: str) -> str:
    """Map a CycleCloud node state to a domain status string."""
    return _CC_STATE_MAP.get(state, "unknown")


def _coerce_bool(value: Any) -> bool:
    """Parse common config-style boolean inputs."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return bool(value)


@injectable
class CycleCloudHandler(AzureHandler):
    """Handler that manages nodes in an Azure CycleCloud cluster.

    ``provider_api = "CycleCloud"``

    This handler communicates with the CycleCloud REST API to:
    - Add nodes to a cluster's node array (``acquire_hosts``)
    - Query node status (``check_hosts_status``)
    - Remove/terminate nodes (``release_hosts``)

    The CycleCloud API credentials and URL are resolved from the template
    or from the Azure provider configuration.
    """

    # ------------------------------------------------------------------
    # Internal: CycleCloud REST API helpers
    # ------------------------------------------------------------------

    def _resolve_cc_config_value(
        self,
        *,
        template: Optional[AzureTemplate],
        metadata: Optional[dict[str, Any]],
        context: Optional[dict[str, Any]],
        provider_cfg: Optional[BaseModel],
        template_attr: str,
        metadata_key: str,
        context_key: str,
        provider_path: tuple[str, ...],
        default: Any = None,
    ) -> Any:
        if template is not None:
            value = getattr(template, template_attr, None)
            if value not in (None, ""):
                return value
        if metadata and metadata.get(metadata_key) not in (None, ""):
            return metadata.get(metadata_key)
        if context and context.get(context_key) not in (None, ""):
            return context.get(context_key)
        current: Any = provider_cfg
        for key in provider_path:
            if current is None:
                break
            current = getattr(current, key, None)
        return default if current in (None, "") else current

    def _get_azure_bearer_token(self, scopes: list[str]) -> Optional[str]:
        try:
            credential = self.azure_client.credential
        except Exception:
            return None

        for scope in scopes:
            if not scope:
                continue
            try:
                token = credential.get_token(scope)
                if getattr(token, "token", None):
                    self._logger.debug("Resolved CycleCloud bearer token via Azure credential scope=%s", scope)
                    return token.token
            except Exception:
                continue
        return None

    def _get_provider_cyclecloud_config(self) -> Optional[BaseModel]:
        provider_cfg = getattr(self.azure_client, "_azure_config", None)
        if isinstance(provider_cfg, BaseModel):
            return provider_cfg

        loader = getattr(self.azure_client, "_get_selected_azure_provider_config", None)
        if callable(loader):
            try:
                loaded_cfg = loader()
                if isinstance(loaded_cfg, BaseModel):
                    return loaded_cfg
            except Exception:
                pass

        return None

    def _load_cc_credential_file(self, credential_path: str) -> dict[str, Any]:
        path = Path(credential_path).expanduser()
        try:
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as exc:
            raise CycleCloudConnectionError(
                f"CycleCloud credential file not found: {path}",
                url=None,
            ) from exc
        except json.JSONDecodeError as exc:
            raise CycleCloudConnectionError(
                f"CycleCloud credential file is not valid JSON: {path}",
                url=None,
            ) from exc
        except OSError as exc:
            raise CycleCloudConnectionError(
                f"Failed to read CycleCloud credential file {path}: {exc}",
                url=None,
            ) from exc

        if not isinstance(data, dict):
            raise CycleCloudConnectionError(
                f"CycleCloud credential file must contain a JSON object: {path}",
                url=None,
            )

        return data

    @staticmethod
    def _credential_file_value(data: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = data.get(key)
            if value not in (None, ""):
                return value
        return None

    def _build_cc_session(
        self,
        *,
        cc_url: Optional[str],
        verify_ssl: Optional[bool],
        template: Optional[AzureTemplate] = None,
        metadata: Optional[dict[str, Any]] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> CycleCloudSessionContext:
        provider_cfg = self._get_provider_cyclecloud_config()
        credential_path = self._resolve_cc_config_value(
            template=template,
            metadata=metadata,
            context=context,
            provider_cfg=provider_cfg,
            template_attr="cyclecloud_credential_path",
            metadata_key="cyclecloud_credential_path",
            context_key="cyclecloud_credential_path",
            provider_path=("cyclecloud", "credential_path"),
        )
        credential_file_data: dict[str, Any] = {}
        if credential_path:
            credential_file_data = self._load_cc_credential_file(str(credential_path))

        cc_url = cc_url or self._resolve_cc_config_value(
            template=template,
            metadata=metadata,
            context=context,
            provider_cfg=provider_cfg,
            template_attr="cyclecloud_url",
            metadata_key="cyclecloud_url",
            context_key="cyclecloud_url",
            provider_path=("cyclecloud", "url"),
        )
        cc_url = cc_url or self._credential_file_value(credential_file_data, "cyclecloud_url", "url")

        if verify_ssl is None:
            verify_resolved = self._resolve_cc_config_value(
                template=template,
                metadata=metadata,
                context=context,
                provider_cfg=provider_cfg,
                template_attr="cyclecloud_verify_ssl",
                metadata_key="cyclecloud_verify_ssl",
                context_key="cyclecloud_verify_ssl",
                provider_path=("cyclecloud", "verify_ssl"),
            )
            if verify_resolved in (None, ""):
                verify_resolved = self._credential_file_value(
                    credential_file_data,
                    "cyclecloud_verify_ssl",
                    "verify_ssl",
                )
            if verify_resolved in (None, ""):
                verify_resolved = True
            verify_ssl = _coerce_bool(verify_resolved)

        if not cc_url:
            raise CycleCloudConnectionError(
                "cyclecloud_url is required in the template, request context, or provider configuration.",
                url=None,
            )

        base_url = cc_url.rstrip("/")
        session = requests.Session()
        session.verify = _coerce_bool(verify_ssl)
        session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        resolved_credential_path = (
            str(credential_path) if credential_path not in (None, "") else None
        )

        auth_mode = self._resolve_cc_config_value(
            template=template,
            metadata=metadata,
            context=context,
            provider_cfg=provider_cfg,
            template_attr="cyclecloud_auth_mode",
            metadata_key="cyclecloud_auth_mode",
            context_key="cyclecloud_auth_mode",
            provider_path=("cyclecloud", "auth_mode"),
        )
        auth_mode = auth_mode or self._credential_file_value(
            credential_file_data,
            "cyclecloud_auth_mode",
            "auth_mode",
        )
        auth_mode = str(auth_mode).strip().lower() if auth_mode else None

        explicit_bearer = self._resolve_cc_config_value(
            template=template,
            metadata=metadata,
            context=context,
            provider_cfg=provider_cfg,
            template_attr="cyclecloud_bearer_token",
            metadata_key="cyclecloud_bearer_token",
            context_key="cyclecloud_bearer_token",
            provider_path=("cyclecloud", "bearer_token"),
        )
        explicit_bearer = explicit_bearer or self._credential_file_value(
            credential_file_data,
            "cyclecloud_bearer_token",
            "bearer_token",
        )

        aad_scope = self._resolve_cc_config_value(
            template=template,
            metadata=metadata,
            context=context,
            provider_cfg=provider_cfg,
            template_attr="cyclecloud_aad_scope",
            metadata_key="cyclecloud_aad_scope",
            context_key="cyclecloud_aad_scope",
            provider_path=("cyclecloud", "aad_scope"),
        )
        aad_scope = aad_scope or self._credential_file_value(
            credential_file_data,
            "cyclecloud_aad_scope",
            "aad_scope",
        )

        if auth_mode == "ssh":
            raise CycleCloudConnectionError(
                "cyclecloud_auth_mode=ssh is not supported. Configure CycleCloud API credentials instead.",
                url=base_url,
            )

        cc_user = self._credential_file_value(
            credential_file_data,
            "cyclecloud_username",
            "username",
        )
        cc_pass = self._credential_file_value(
            credential_file_data,
            "cyclecloud_password",
            "password",
        )
        resolved_auth_mode: Optional[str] = None
        if cc_user and cc_pass and auth_mode != "bearer":
            session.auth = (cc_user, cc_pass)
            resolved_auth_mode = "basic"
        else:
            bearer_token = explicit_bearer
            if not bearer_token:
                parsed = urlparse(base_url)
                host_scope = f"{parsed.scheme}://{parsed.netloc}/.default" if parsed.scheme and parsed.netloc else ""
                scopes = [str(aad_scope)] if aad_scope else []
                scopes.extend([host_scope, "https://management.azure.com/.default"])
                bearer_token = self._get_azure_bearer_token(scopes)

            if bearer_token:
                session.headers["Authorization"] = f"Bearer {bearer_token}"
                resolved_auth_mode = "bearer"
            elif auth_mode == "bearer":
                raise CycleCloudConnectionError(
                    "cyclecloud_auth_mode=bearer requested but no bearer token could be resolved.",
                    url=base_url,
                )
            else:
                raise CycleCloudConnectionError(
                    "No CycleCloud auth method resolved. Provide username/password or a bearer token/Azure credential.",
                    url=base_url,
                )

        return CycleCloudSessionContext(
            session=session,
            base_url=base_url,
            auth_mode=resolved_auth_mode,
            credential_path=resolved_credential_path,
        )

    def _get_cc_session(self, template: AzureTemplate) -> CycleCloudSessionContext:
        return self._build_cc_session(
            cc_url=template.cyclecloud_url,
            verify_ssl=template.cyclecloud_verify_ssl,
            template=template,
        )

    @staticmethod
    def _cc_request_raw(
        session: requests.Session,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = session.request(method, url, timeout=30, **kwargs)
            response.raise_for_status()
            body: Any = {}
            if response.content:
                try:
                    body = response.json()
                except requests.exceptions.JSONDecodeError as exc:
                    raise CycleCloudConnectionError(
                        f"CycleCloud API returned invalid JSON from {url}: {exc}",
                        url=url,
                    ) from exc
            return {
                "body": body,
                "headers": dict(response.headers),
                "status_code": response.status_code,
                "url": url,
            }
        except requests.exceptions.ConnectionError as exc:
            raise CycleCloudConnectionError(
                f"Cannot connect to CycleCloud at {url}: {exc}",
                url=url,
            ) from exc
        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            body = ""
            body_json: dict[str, Any] | None = None
            if exc.response is not None:
                body = exc.response.text
                try:
                    body_json = exc.response.json()
                except requests.exceptions.JSONDecodeError:
                    body_json = None

            error_code = None
            if isinstance(body_json, dict):
                error_code = (
                    body_json.get("code")
                    or (body_json.get("error") or {}).get("code")
                    or body_json.get("type")
                )

            raise CycleCloudConnectionError(
                f"CycleCloud API error (HTTP {status_code}): {body}",
                url=url,
                details={
                    "status_code": status_code,
                    "body": body,
                    "body_json": body_json,
                    "error_code": error_code,
                },
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise CycleCloudConnectionError(
                f"CycleCloud API request failed: {exc}",
                url=url,
            ) from exc

    @staticmethod
    def _cc_request(
        session: requests.Session,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> Any:
        return CycleCloudHandler._cc_request_raw(session, method, url, **kwargs)["body"]

    @staticmethod
    def _cc_request_with_metadata(
        session: requests.Session,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return CycleCloudHandler._cc_request_raw(session, method, url, **kwargs)

    def _resolve_release_node_names(
        self,
        *,
        session: requests.Session,
        base_url: str,
        cluster_name: str,
        machine_ids: list[str],
    ) -> list[str]:
        """Resolve stored machine IDs to CycleCloud node names for release calls."""
        try:
            nodes_response = self._cc_request(
                session,
                "GET",
                f"{base_url}/clusters/{cluster_name}/nodes",
            )
        except CycleCloudConnectionError:
            return machine_ids

        nodes = nodes_response.get("nodes", [])
        resolved_names: list[str] = []
        seen: set[str] = set()

        for machine_id in machine_ids:
            resolved_name = machine_id
            for node in nodes:
                node_name = node.get("Name", "")
                node_id = node.get("NodeId", "")
                if machine_id == node_name or machine_id == node_id:
                    resolved_name = node_name or node_id or machine_id
                    break
            if resolved_name and resolved_name not in seen:
                resolved_names.append(resolved_name)
                seen.add(resolved_name)

        if resolved_names != machine_ids:
            self._logger.info(
                "Resolved CycleCloud release ids %s -> node names %s",
                machine_ids,
                resolved_names,
            )

        return resolved_names

    @staticmethod
    def _extract_cyclecloud_node_errors(
        node: dict[str, Any],
        *,
        cluster_name: str,
        node_array: str,
    ) -> list[dict[str, Any]]:
        """Extract structured node errors from CycleCloud node payloads."""
        state = str(node.get("State") or node.get("status") or "Unknown")
        message = (
            node.get("Message")
            or node.get("StatusMessage")
            or node.get("Error")
            or node.get("FailureMessage")
        )
        error_code = node.get("ErrorCode") or ("NodeFailed" if state == "Failed" else None)

        if not error_code and not message:
            return []
        if state != "Failed" and not message:
            return []

        node_id = node.get("name") or node.get("Name") or node.get("nodeId") or node.get("NodeId")
        return [{
            "error_code": str(error_code or "CycleCloudNodeError"),
            "error_message": str(message or f"CycleCloud node entered state {state}"),
            "instance_id": node_id,
            "resource_id": cluster_name,
            "node_array": node_array,
            "cc_state": state,
        }]

    # ------------------------------------------------------------------
    # acquire_hosts
    # ------------------------------------------------------------------

    def acquire_hosts(
        self, request: Request, template: AzureTemplate
    ) -> dict[str, Any]:
        """Add nodes to a CycleCloud cluster's node array.

        Uses the CycleCloud REST API ``POST /clusters/{cluster}/nodes/create``
        endpoint to request new nodes in the specified node array.

        Returns:
            Standard handler result dict with success, resource_ids,
            instances, error_message, and provider_data.
        """
        cluster_name = template.cluster_name
        node_array = template.node_array
        count = request.requested_count
        vm_size = template.vm_size

        if not cluster_name:
            raise CycleCloudNodeError(
                "cluster_name is required for CycleCloud provisioning.",
                cluster_name=cluster_name or "",
                node_array=node_array,
            )

        self._logger.info(
            "Adding %d node(s) to CycleCloud cluster '%s' node array '%s' "
            "(vm_size=%s)",
            count,
            cluster_name,
            node_array,
            vm_size,
        )

        session_context = self._get_cc_session(template)
        session = session_context.session
        base_url = session_context.base_url

        # Verify the cluster exists
        try:
            cluster_status = self._cc_request(
                session,
                "GET",
                f"{base_url}/clusters/{cluster_name}/status",
            )
            cluster_state = cluster_status.get("state", "Unknown")
            self._logger.debug(
                "CycleCloud cluster '%s' state: %s", cluster_name, cluster_state
            )
        except CycleCloudConnectionError as exc:
            if exc.details and exc.details.get("status_code") == 404:
                raise CycleCloudClusterNotFoundError(
                    f"CycleCloud cluster '{cluster_name}' not found.",
                    cluster_name=cluster_name,
                ) from exc
            raise

        # Build the node creation request
        # CycleCloud REST API: POST /clusters/{cluster}/nodes/create
        definition: dict[str, Any] = {}
        # When multiple VM sizes are provided, let the CycleCloud node array
        # select an eligible bucket instead of pinning a single machine type.
        if not template.vm_sizes:
            definition["machineType"] = vm_size

        node_params: dict[str, Any] = {
            "sets": [
                {
                    "count": count,
                    "nodearray": node_array,
                    "definition": definition,
                }
            ],
        }

        # Add node attributes from template if specified
        if template.node_attributes:
            node_params["sets"][0]["definition"].update(template.node_attributes)

        # Add subnet if specified
        if template.subnet_ids:
            subnet_id = template.subnet_ids[0]
            if subnet_id and subnet_id != "default-subnet":
                node_params["sets"][0]["definition"]["SubnetId"] = subnet_id
        elif template.network_config:
            node_params["sets"][0]["definition"]["SubnetId"] = (
                template.network_config.subnet_id
            )

        try:
            create_response = self._cc_request_with_metadata(
                session,
                "POST",
                f"{base_url}/clusters/{cluster_name}/nodes/create",
                json=node_params,
            )
            result = create_response.get("body") or {}
        except CycleCloudConnectionError as exc:
            raise CycleCloudNodeError(
                f"Failed to add nodes to cluster '{cluster_name}': {exc}",
                cluster_name=cluster_name,
                node_array=node_array,
            ) from exc

        # Extract request tracking metadata from the response.
        # CycleCloud create is asynchronous; real node identities are discovered later
        # via the operation URL or filtered node-list APIs.
        operation_id = result.get("operationId", "")
        operation_location = create_response.get("headers", {}).get("Location")
        created_sets = result.get("sets", [])
        fleet_errors: list[dict[str, Any]] = []
        added_count = 0

        for node_set in created_sets:
            added = node_set.get("added", 0)
            added_count += int(added or 0)
            set_nodes = node_set.get("nodes", [])
            for node in set_nodes:
                node_errors = self._extract_cyclecloud_node_errors(
                    node,
                    cluster_name=cluster_name,
                    node_array=node_array,
                )
                for error in node_errors:
                    if error not in fleet_errors:
                        fleet_errors.append(error)

        self._logger.info(
            "CycleCloud node request accepted for cluster '%s': operation_id=%s, added=%d",
            cluster_name,
            operation_id,
            added_count,
        )

        # resource_ids: use cluster_name as the primary resource identifier.
        # Individual node identities are discovered later via status queries.
        resource_ids = [cluster_name]

        return {
            "success": True,
            "resource_ids": resource_ids,
            "instances": [],
            "error_message": None,
            "provider_data": {
                "cluster_name": cluster_name,
                "node_array": node_array,
                "operation_id": operation_id,
                "operation_location": operation_location,
                "added_count": added_count,
                "resource_group": template.resource_group,
                "location": template.location,
                "fleet_errors": fleet_errors,
                "cyclecloud_url": base_url,
                "cyclecloud_credential_path": session_context.credential_path,
                "cyclecloud_verify_ssl": bool(session.verify),
                "cyclecloud_auth_mode": session_context.auth_mode,
                "cyclecloud_aad_scope": getattr(template, "cyclecloud_aad_scope", None),
            },
        }

    # ------------------------------------------------------------------
    # check_hosts_status
    # ------------------------------------------------------------------

    def check_hosts_status(self, request: Request) -> list[dict[str, Any]]:
        """Check status of nodes in a CycleCloud cluster.

        Uses CycleCloud REST API ``GET /clusters/{cluster}/nodes`` to
        retrieve the current state of all nodes, then filters to those
        belonging to the request's resource IDs.
        """
        resource_ids: list[str] = getattr(request, "resource_ids", []) or []
        if not resource_ids:
            self._logger.warning("check_hosts_status called with no resource_ids")
            return []

        metadata = request.metadata or {}
        cluster_name = metadata.get("cluster_name")
        node_array = metadata.get("node_array")
        node_ids = metadata.get("node_ids", [])
        operation_id = metadata.get("operation_id")
        operation_location = metadata.get("operation_location")

        if not cluster_name:
            # resource_ids[0] is the cluster name for CycleCloud
            cluster_name = resource_ids[0] if resource_ids else None

        if not cluster_name:
            self._logger.error("Cannot determine cluster_name for status check")
            return []

        # Build a minimal template to get CycleCloud connection info
        cc_url = metadata.get("cyclecloud_url")
        cc_verify = metadata.get("cyclecloud_verify_ssl", None)

        try:
            session_context = self._build_cc_session(
                cc_url=cc_url,
                verify_ssl=cc_verify,
                metadata=metadata,
            )
            session = session_context.session
            base_url = session_context.base_url
        except CycleCloudConnectionError as exc:
            self._logger.error(
                "Failed to build CycleCloud session for status check (cluster '%s'): %s",
                cluster_name,
                exc,
            )
            raise

        all_nodes: list[dict[str, Any]] = []

        if operation_location:
            try:
                operation_response = self._cc_request(
                    session,
                    "GET",
                    str(operation_location),
                )
                if isinstance(operation_response, dict):
                    all_nodes = operation_response.get("nodes", []) or []
            except CycleCloudConnectionError as exc:
                self._logger.debug(
                    "Failed to query CycleCloud operation URL '%s': %s",
                    operation_location,
                    exc,
                )

        if not all_nodes:
            try:
                nodes_url = f"{base_url}/clusters/{cluster_name}/nodes"
                request_kwargs: dict[str, Any] = {}
                if operation_id:
                    request_kwargs["params"] = {"operation": operation_id}
                nodes_response = self._cc_request(
                    session,
                    "GET",
                    nodes_url,
                    **request_kwargs,
                )
            except CycleCloudConnectionError as exc:
                self._logger.error(
                    "Failed to get node status for cluster '%s': %s",
                    cluster_name,
                    exc,
                )
                raise

            all_nodes = nodes_response.get("nodes", [])
        results: list[dict[str, Any]] = []

        for node in all_nodes:
            node_name = node.get("Name", "")
            node_id = node.get("NodeId", node_name)

            # Filter by node array if specified
            node_na = node.get("NodeArray", "")
            if node_array and node_na != node_array:
                continue

            # Filter by known node IDs if we have them
            if node_ids and node_name not in node_ids and node_id not in node_ids:
                continue

            cc_state = node.get("State", "Unknown")
            status = _resolve_cc_state(cc_state)

            private_ip = node.get("PrivateIp")
            public_ip = node.get("PublicIp")
            machine_type = node.get("MachineType") or "unknown"
            fleet_errors = self._extract_cyclecloud_node_errors(
                node,
                cluster_name=cluster_name,
                node_array=node_na,
            )

            results.append({
                "instance_id": node_name or node_id,
                "status": status,
                "private_ip": private_ip,
                "public_ip": public_ip,
                "launch_time": node.get("CreateTime"),
                "instance_type": machine_type,
                "subnet_id": node.get("SubnetId"),
                "vpc_id": None,
                "availability_zone": None,
                "provider_type": "azure",
                "provider_data": {
                    "resource_id": cluster_name,
                    "cluster_name": cluster_name,
                    "node_array": node_na,
                    "node_id": node_id,
                    "cc_state": cc_state,
                    "hostname": node.get("Hostname"),
                    "fleet_errors": fleet_errors,
                },
            })

        self._logger.debug(
            "CycleCloud status check for cluster '%s': %d node(s) found",
            cluster_name,
            len(results),
        )

        return results

    # ------------------------------------------------------------------
    # release_hosts
    # ------------------------------------------------------------------

    def release_hosts(
        self,
        machine_ids: list[str],
        resource_id: str,
        context: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Remove/terminate nodes from a CycleCloud cluster.

        Uses CycleCloud REST API to deallocate and remove specific nodes.

        Args:
            machine_ids: Node names/IDs to remove.
            resource_id: The cluster name.
            context: Must contain ``cyclecloud_url`` and optionally
                ``cyclecloud_credential_path``, ``cyclecloud_verify_ssl``.
        """
        context = context or {}
        cluster_name = resource_id

        cc_url = context.get("cyclecloud_url")
        cc_verify = context.get("cyclecloud_verify_ssl", None)

        try:
            session_context = self._build_cc_session(
                cc_url=cc_url,
                verify_ssl=cc_verify,
                context=context,
            )
            session = session_context.session
            base_url = session_context.base_url
        except CycleCloudConnectionError as exc:
            raise TerminationError(
                f"Failed to build CycleCloud session for release_hosts: {exc}",
                resource_ids=machine_ids,
            ) from exc

        self._logger.info(
            "Removing %d node(s) from CycleCloud cluster '%s': %s",
            len(machine_ids),
            cluster_name,
            machine_ids,
        )

        node_names = self._resolve_release_node_names(
            session=session,
            base_url=base_url,
            cluster_name=cluster_name,
            machine_ids=machine_ids,
        )

        # CycleCloud REST API: POST /clusters/{cluster}/nodes/deallocate
        # Then: POST /clusters/{cluster}/nodes/remove
        # Deallocate first, then remove for clean shutdown.
        try:
            # Step 1: Deallocate (graceful shutdown)
            deallocate_payload: dict[str, Any] = {
                "names": node_names,
            }
            deallocate_response = self._cc_request_with_metadata(
                session,
                "POST",
                f"{base_url}/clusters/{cluster_name}/nodes/deallocate",
                json=deallocate_payload,
            )
            self._logger.debug(
                "Deallocate request sent for nodes: %s", node_names
            )

            # Step 2: Remove the nodes from the cluster
            remove_payload: dict[str, Any] = {
                "names": node_names,
            }
            remove_response = self._cc_request_with_metadata(
                session,
                "POST",
                f"{base_url}/clusters/{cluster_name}/nodes/remove",
                json=remove_payload,
            )
            self._logger.info(
                "Successfully removed %d node(s) from cluster '%s'",
                len(machine_ids),
                cluster_name,
            )
            return {
                "provider_data": {
                    "cluster_name": cluster_name,
                    "deallocate_operation_location": (
                        deallocate_response.get("headers", {}).get("Location")
                    ),
                    "remove_operation_location": (
                        remove_response.get("headers", {}).get("Location")
                    ),
                    "operation_status": "submitted",
                }
            }

        except CycleCloudConnectionError as exc:
            raise TerminationError(
                f"Failed to remove nodes from CycleCloud cluster "
                f"'{cluster_name}': {exc}",
                resource_ids=machine_ids,
            ) from exc

    # ------------------------------------------------------------------
    # Example templates
    # ------------------------------------------------------------------

    @classmethod
    def get_example_templates(cls) -> list[dict[str, Any]]:
        return [
            {
                "template_id": "azure-cyclecloud-hpc",
                "name": "Azure CycleCloud HPC Cluster",
                "description": "Add HPC nodes to an existing CycleCloud cluster",
                "provider_type": "azure",
                "provider_api": AzureProviderApi.CYCLECLOUD.value,
                "vm_size": "Standard_HB120rs_v3",
                "resource_group": "my-resource-group",
                "location": "eastus2",
                "cluster_name": "my-hpc-cluster",
                "node_array": "hpc",
                "cyclecloud_url": "https://cyclecloud.example.com",
                "max_instances": 100,
            },
            {
                "template_id": "azure-cyclecloud-htc",
                "name": "Azure CycleCloud HTC Cluster",
                "description": "Add HTC (high-throughput) nodes to a CycleCloud cluster",
                "provider_type": "azure",
                "provider_api": AzureProviderApi.CYCLECLOUD.value,
                "vm_size": "Standard_D4s_v5",
                "resource_group": "my-resource-group",
                "location": "eastus2",
                "cluster_name": "my-htc-cluster",
                "node_array": "htc",
                "cyclecloud_url": "https://cyclecloud.example.com",
                "max_instances": 500,
            },
        ]
