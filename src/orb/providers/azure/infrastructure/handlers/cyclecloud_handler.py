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

from contextlib import AbstractContextManager
from typing import Any, Callable, Optional

import requests
from orb.domain.base.dependency_injection import injectable
from orb.domain.request.aggregate import Request
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions.azure_exceptions import (
    AuthenticationError,
    CycleCloudClusterNotFoundError,
    CycleCloudConnectionError,
    CycleCloudNodeError,
    TerminationError,
)
from orb.providers.azure.infrastructure.cyclecloud_session import (
    CycleCloudRequestContext,
    CycleCloudSessionContext,
    CycleCloudSessionSettings,
)
from orb.providers.azure.infrastructure.cyclecloud_session_builder import (
    CycleCloudSessionBuilder,
)
from orb.providers.azure.infrastructure.credential_factory import (
    AzureCredentialAccessTokenProvider,
)
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureHandler
from orb.providers.infrastructure.error_codes import (
    ProviderErrorEntry,
    collect_provider_error_codes,
)


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


def resolve_cc_state(state: str) -> str:
    """Map a CycleCloud node state to a domain status string."""
    return _CC_STATE_MAP.get(state, "unknown")


class _CycleCloudSessionScope(AbstractContextManager[CycleCloudSessionContext]):
    """Own a CycleCloud requests session for the duration of a handler flow."""

    def __init__(
        self,
        build_session: Callable[[], CycleCloudSessionContext],
    ):
        self._build_session = build_session
        self._session_context: Optional[CycleCloudSessionContext] = None

    def __enter__(self) -> CycleCloudSessionContext:
        self._session_context = self._build_session()
        return self._session_context

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if self._session_context is not None:
            self._session_context.session.close()
        return None


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

    def _get_provider_cyclecloud_config(self) -> Optional[AzureProviderConfig]:
        loaded_cfg = self.azure_client.get_provider_config()
        if isinstance(loaded_cfg, AzureProviderConfig):
            return loaded_cfg
        return None

    def _get_cc_request_timeout(self) -> tuple[int, int]:
        provider_cfg = self._get_provider_cyclecloud_config()
        if provider_cfg is None:
            return 30, 60
        return provider_cfg.connect_timeout, provider_cfg.read_timeout

    @staticmethod
    def _configure_cc_session_auth(
            *,
        session: requests.Session,
        settings: CycleCloudSessionSettings,
    ) -> Optional[str]:
        auth_mode = settings.auth_mode

        if auth_mode == "ssh":
            raise CycleCloudConnectionError(
                "cyclecloud_auth_mode=ssh is not supported. Configure CycleCloud API credentials instead.",
                url=settings.base_url,
            )

        cc_user, cc_pass = settings.username, settings.password
        if cc_user and cc_pass and auth_mode != "bearer":
            session.auth = (cc_user, cc_pass)
            return "basic"

        bearer_token = settings.bearer_token
        if bearer_token:
            session.headers["Authorization"] = f"Bearer {bearer_token}"
            return "bearer"
        if auth_mode == "bearer":
            raise CycleCloudConnectionError(
                "cyclecloud_auth_mode=bearer requested but no bearer token could be resolved.",
                url=settings.base_url,
            )
        raise CycleCloudConnectionError(
            "No CycleCloud auth method resolved. Provide username/password or a bearer token/Azure credential.",
            url=settings.base_url,
        )

    def _build_cc_session(
        self,
        *,
        cc_url: Optional[str],
        verify_ssl: Optional[bool],
        template: Optional[AzureTemplate] = None,
        request_context: Optional[CycleCloudRequestContext] = None,
    ) -> CycleCloudSessionContext:
        provider_cfg = self._get_provider_cyclecloud_config()
        token_provider = None
        try:
            credential = self.azure_client.credential
        except AuthenticationError:
            credential = None
        if credential is not None:
            token_provider = AzureCredentialAccessTokenProvider(credential)
        settings = CycleCloudSessionBuilder(
            cc_url=cc_url,
            verify_ssl=verify_ssl,
            template=template,
            request_context=request_context,
            provider_cfg=provider_cfg,
            token_provider=token_provider,
        ).build_settings()
        session: Optional[requests.Session] = None
        try:
            session = requests.Session()
            session.verify = settings.verify_ssl
            session.headers.update({
                "Accept": "application/json",
                "Content-Type": "application/json",
            })
            resolved_auth_mode = self._configure_cc_session_auth(
                session=session,
                settings=settings,
            )
        except Exception:
            if session is not None:
                session.close()
            raise

        return CycleCloudSessionContext(
            session=session,
            base_url=settings.base_url,
            auth_mode=resolved_auth_mode,
            credential_path=settings.credential_path,
        )

    def _cc_session_scope(
        self,
        *,
        cc_url: Optional[str],
        verify_ssl: Optional[bool],
        template: Optional[AzureTemplate] = None,
        request_context: Optional[CycleCloudRequestContext] = None,
    ) -> AbstractContextManager[CycleCloudSessionContext]:
        return _CycleCloudSessionScope(
            lambda: self._build_cc_session(
                cc_url=cc_url,
                verify_ssl=verify_ssl,
                template=template,
                request_context=request_context,
            )
        )

    def _cc_request_raw(
        self,
        session: requests.Session,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = session.request(
                method,
                url,
                timeout=self._get_cc_request_timeout(),
                **kwargs,
            )
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

    def _cc_request(
        self,
        session: requests.Session,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> Any:
        return self._cc_request_raw(session, method, url, **kwargs)["body"]

    def _cc_request_with_metadata(
        self,
        session: requests.Session,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self._cc_request_raw(session, method, url, **kwargs)

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
                if machine_id in {node_name, node_id}:
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
    ) -> list[ProviderErrorEntry]:
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
        node_error: ProviderErrorEntry = {
            "error_code": str(error_code or "CycleCloudNodeError"),
            "error_message": str(message or f"CycleCloud node entered state {state}"),
            "resource_id": cluster_name,
            "node_array": node_array,
            "cc_state": state,
        }
        if node_id not in (None, ""):
            node_error["instance_id"] = str(node_id)
        return [node_error]

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

        with self._cc_session_scope(
            cc_url=template.cyclecloud_url,
            verify_ssl=template.cyclecloud_verify_ssl,
            template=template,
        ) as session_context:
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

            cyclecloud_request_id = str(request.request_id)
            node_params: dict[str, Any] = {
                "requestId": cyclecloud_request_id,
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
        fleet_errors: list[ProviderErrorEntry] = []
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

        # Persist the request-scoped CycleCloud identity durably.
        resource_ids = [cyclecloud_request_id]

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
                "resource_group": template.resource_group.value,
                "location": template.location.value,
                "error_codes": collect_provider_error_codes(fleet_errors),
                "fleet_errors": fleet_errors,
                "cyclecloud_url": base_url,
                "cyclecloud_credential_path": session_context.credential_path,
                "cyclecloud_verify_ssl": bool(session.verify),
                "cyclecloud_auth_mode": session_context.auth_mode,
                "cyclecloud_aad_scope": template.cyclecloud_aad_scope,
            },
        }

    # ------------------------------------------------------------------
    # check_hosts_status
    # ------------------------------------------------------------------

    def check_hosts_status(self, request: Request) -> list[dict[str, Any]]:
        """Check status of nodes in a CycleCloud cluster.

        Uses CycleCloud REST API ``GET /clusters/{cluster}/nodes`` with the
        durable request-scoped ``request_id`` filter.
        """
        resource_ids = request.resource_ids
        if not resource_ids:
            self._logger.warning("check_hosts_status called with no resource_ids")
            return []

        metadata = request.metadata or {}
        request_context = CycleCloudRequestContext.from_mapping(metadata)
        cluster_name = request_context.cluster_name
        node_array = request_context.node_array
        node_ids = list(request_context.node_ids)
        cyclecloud_request_id = resource_ids[0]

        if not cluster_name:
            message = "cluster_name is required for CycleCloud status check"
            self._logger.error(message)
            raise CycleCloudConnectionError(
                message,
                url=request_context.cyclecloud_url,
                details={"request_id": request.request_id},
            )

        if not cyclecloud_request_id:
            message = (
                f"CycleCloud request identity is required for status check in cluster '{cluster_name}'"
            )
            self._logger.error(message)
            raise CycleCloudConnectionError(
                message,
                url=request_context.cyclecloud_url,
                details={"resource_ids": resource_ids},
            )

        try:
            with self._cc_session_scope(
                cc_url=request_context.cyclecloud_url,
                verify_ssl=request_context.cyclecloud_verify_ssl,
                request_context=request_context,
            ) as session_context:
                session = session_context.session
                base_url = session_context.base_url
                try:
                    nodes_response = self._cc_request(
                        session,
                        "GET",
                        f"{base_url}/clusters/{cluster_name}/nodes",
                        params={"request_id": cyclecloud_request_id},
                    )
                except CycleCloudConnectionError as exc:
                    self._logger.error(
                        "Failed to get node status for cluster '%s' and request_id '%s': %s",
                        cluster_name,
                        cyclecloud_request_id,
                        exc,
                    )
                    raise
        except CycleCloudConnectionError as exc:
            self._logger.error(
                "Failed to build CycleCloud session for status check (cluster '%s'): %s",
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
            status = resolve_cc_state(cc_state)
            if status == "unknown":
                self._logger.warning("Unmapped CycleCloud node state: %s", cc_state)

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
        request_context = CycleCloudRequestContext.from_mapping(context)
        cluster_name = resource_id

        try:
            with self._cc_session_scope(
                cc_url=request_context.cyclecloud_url,
                verify_ssl=request_context.cyclecloud_verify_ssl,
                request_context=request_context,
            ) as session_context:
                session = session_context.session
                base_url = session_context.base_url

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

                # CycleCloud exposes deallocate and remove as distinct operations.
                # If deallocate succeeds and remove fails, nodes can remain visibly
                # deallocating/deallocated until the follow-up remove succeeds.
                try:
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
        except CycleCloudConnectionError as exc:
            raise TerminationError(
                f"Failed to build CycleCloud session for release_hosts: {exc}",
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
