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
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from orb.domain.base.ports import LoggingPort
from orb.domain.request.aggregate import Request
from orb.infrastructure.di.injectable import injectable
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
from orb.providers.azure.infrastructure.credential_factory import (
    AsyncAzureCredentialAccessTokenProvider,
)
from orb.providers.azure.infrastructure.cyclecloud_session import (
    AsyncCycleCloudSessionContext,
    CycleCloudRequestContext,
)
from orb.providers.azure.infrastructure.cyclecloud_session_builder import (
    CycleCloudSessionBuilder,
)
from orb.providers.azure.infrastructure.error_codes import (
    ProviderErrorEntry,
    collect_provider_error_codes,
)
from orb.providers.azure.infrastructure.handlers.azure_handler import (
    AzureAcquireHostsResult,
    AzureHandler,
    AzureHandlerStatusResult,
    AzureReleaseContext,
    AzureReleaseHostsResult,
    AzureStatusProviderData,
)

# CycleCloud node state → domain status mapping
_CC_STATE_MAP: dict[str, str] = {
    "Off": "stopped",
    "Acquiring": "pending",
    "Preparing": "pending",
    "Starting": "pending",
    "Started": "running",
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


@dataclass(frozen=True)
class CycleCloudNode:
    """Normalized CycleCloud node payload used inside the handler."""

    name: str
    node_id: str
    node_array: str
    state: str
    private_ip: Optional[str]
    public_ip: Optional[str]
    machine_type: str
    create_time: Optional[str]
    subnet_id: Optional[str]
    hostname: Optional[str]
    error_message: Optional[str]
    error_code: Optional[str]


@dataclass(frozen=True)
class _CycleCloudAcquireRequest:
    """Normalized request payload inputs for CycleCloud node creation."""

    cluster_name: str
    node_array: str
    vm_size: str
    count: int
    cyclecloud_request_id: str
    node_params: dict[str, Any]


@dataclass(frozen=True)
class _CycleCloudStatusRequest:
    """Durable request-scoped context needed for CycleCloud status checks."""

    request_context: CycleCloudRequestContext
    cluster_name: str
    node_array: Optional[str]
    node_ids: list[str]
    cyclecloud_request_id: str


def _optional_text(value: Any) -> Optional[str]:
    """Return a normalized optional string from an external JSON value."""
    if value in (None, ""):
        return None
    return str(value)


def _first_text(node: dict[str, Any], *keys: str, default: str = "") -> str:
    """Return the first populated string value from an external node payload."""
    for key in keys:
        value = _optional_text(node.get(key))
        if value is not None:
            return value
    return default


def _first_optional_text(node: dict[str, Any], *keys: str) -> Optional[str]:
    """Return the first populated optional string value from an external node payload."""
    for key in keys:
        value = _optional_text(node.get(key))
        if value is not None:
            return value
    return None


def _collect_cyclecloud_status_results(
    *,
    logger: LoggingPort,
    cluster_name: str,
    nodes: list[dict[str, Any]],
    node_array: Optional[str],
    node_ids: list[str],
) -> list[AzureHandlerStatusResult]:
    """Filter and normalize CycleCloud node payloads for status responses."""
    results: list[AzureHandlerStatusResult] = []
    for node in nodes:
        parsed_node = _parse_cyclecloud_node(node)
        node_name = parsed_node.name
        node_id = parsed_node.node_id or node_name

        if node_array and parsed_node.node_array != node_array:
            continue
        if node_ids and node_name not in node_ids and node_id not in node_ids:
            continue

        cc_state = parsed_node.state
        status = resolve_cc_state(cc_state)
        if status == "unknown":
            logger.warning("Unmapped CycleCloud node state: %s", cc_state)

        fleet_errors = _extract_cyclecloud_node_errors(
            node,
            cluster_name=cluster_name,
            node_array=parsed_node.node_array,
        )

        results.append(
            _build_cyclecloud_status_result(
                cluster_name=cluster_name,
                parsed_node=parsed_node,
                cc_state=cc_state,
                status=status,
                fleet_errors=fleet_errors,
            )
        )
    return results


def _build_cyclecloud_status_result(
    *,
    cluster_name: str,
    parsed_node: CycleCloudNode,
    cc_state: str,
    status: str,
    fleet_errors: list[ProviderErrorEntry],
) -> AzureHandlerStatusResult:
    """Build a typed Azure status result for one CycleCloud node."""
    node_name = parsed_node.name or parsed_node.hostname or parsed_node.node_id
    node_id = parsed_node.node_id or node_name
    provider_data: AzureStatusProviderData = {
        "resource_id": cluster_name,
        "cloud_host_id": node_id,
        "cluster_name": cluster_name,
        "node_array": parsed_node.node_array,
        "node_id": node_id,
        "cc_state": cc_state,
        "fleet_errors": [dict(error) for error in fleet_errors],
    }
    if parsed_node.name:
        provider_data["node_name"] = parsed_node.name
    if parsed_node.hostname:
        provider_data["hostname"] = parsed_node.hostname
    return {
        "instance_id": node_name,
        "name": node_name,
        "resource_id": cluster_name,
        "status": status,
        "private_ip": parsed_node.private_ip,
        "public_ip": parsed_node.public_ip,
        "launch_time": parsed_node.create_time,
        "instance_type": parsed_node.machine_type,
        "subnet_id": parsed_node.subnet_id,
        "vpc_id": None,
        "tags": {},
        "price_type": None,
        "provider_type": "azure",
        "provider_data": provider_data,
    }


def _parse_cyclecloud_node(node: dict[str, Any]) -> CycleCloudNode:
    """Normalize a CycleCloud node-list payload into a typed object.

    Learn docs underdocument this response shape; in practice CycleCloud has
    returned both lower-camel and PascalCase node fields. Normalize those
    variants once at the provider boundary.
    """
    return CycleCloudNode(
        name=_first_text(node, "name", "Name"),
        node_id=_first_text(node, "nodeId", "NodeId", "id"),
        node_array=_first_text(node, "nodeArray", "NodeArray", "Template"),
        state=_first_text(node, "state", "State", "status", "Status", default="Unknown"),
        private_ip=_first_optional_text(node, "privateIp", "PrivateIp", "ipAddress", "IpAddress"),
        public_ip=_first_optional_text(node, "publicIp", "PublicIp"),
        machine_type=_first_text(node, "machineType", "MachineType", default="unknown"),
        create_time=_first_optional_text(node, "createTime", "CreateTime"),
        subnet_id=_first_optional_text(node, "subnetId", "SubnetId"),
        hostname=_first_optional_text(node, "hostname", "Hostname"),
        error_message=_first_optional_text(
            node,
            "message",
            "Message",
            "error",
            "Error",
            "statusMessage",
            "StatusMessage",
            "failureMessage",
            "FailureMessage",
        ),
        error_code=_first_optional_text(node, "errorCode", "ErrorCode"),
    )


def _extract_cyclecloud_node_errors(
    node: dict[str, Any],
    *,
    cluster_name: str,
    node_array: str,
) -> list[ProviderErrorEntry]:
    """Extract structured node errors from CycleCloud node payloads."""
    parsed_node = _parse_cyclecloud_node(node)
    if parsed_node.state == "Unknown" and (
        node.get("status") not in (None, "") or node.get("id") not in (None, "")
    ):
        parsed_node = _parse_cyclecloud_node_result(node)

    message = parsed_node.error_message
    error_code = parsed_node.error_code or ("NodeFailed" if parsed_node.state == "Failed" else None)

    if not error_code and not message:
        return []
    if parsed_node.state != "Failed" and not message:
        return []

    node_error: ProviderErrorEntry = {
        "error_code": str(error_code or "CycleCloudNodeError"),
        "error_message": str(message or f"CycleCloud node entered state {parsed_node.state}"),
        "resource_id": cluster_name,
        "node_array": node_array,
        "cc_state": parsed_node.state,
    }
    if parsed_node.name or parsed_node.node_id:
        node_error["instance_id"] = parsed_node.name or parsed_node.node_id
    return [node_error]


def _parse_cyclecloud_node_result(node: dict[str, Any]) -> CycleCloudNode:
    """Normalize a CycleCloud node-management result payload into a typed object."""
    return CycleCloudNode(
        name=str(node.get("name") or ""),
        node_id=str(node.get("id") or ""),
        node_array="",
        state=str(node.get("status") or "Unknown"),
        private_ip=None,
        public_ip=None,
        machine_type="unknown",
        create_time=None,
        subnet_id=None,
        hostname=None,
        error_message=_optional_text(node.get("message") or node.get("error")),
        error_code=_optional_text(node.get("errorCode")),
    )


def _response_json_or_error(
    *,
    response: httpx.Response,
    url: str,
    decode_error_types: tuple[type[BaseException], ...],
) -> Any:
    """Parse a CycleCloud JSON response body or raise a normalized connection error."""
    body: Any = {}
    if response.content:
        try:
            body = response.json()
        except decode_error_types as exc:
            raise CycleCloudConnectionError(
                f"CycleCloud API returned invalid JSON from {url}: {exc}",
                url=url,
            ) from exc
    return body


def _response_metadata(response: httpx.Response, *, url: str) -> dict[str, Any]:
    """Normalize response metadata returned from sync or async HTTP transports."""
    return {
        "headers": dict(response.headers),
        "status_code": response.status_code,
        "url": url,
    }


def _http_error_details(body_json: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the stable CycleCloud error details shape from a parsed JSON body."""
    error_code = None
    if isinstance(body_json, dict):
        error_code = (
            body_json.get("code")
            or (body_json.get("error") or {}).get("code")
            or body_json.get("type")
        )
    return {
        "body_json": body_json,
        "error_code": error_code,
    }


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

    async def _build_async_cc_session(
        self,
        *,
        cc_url: Optional[str],
        verify_ssl: Optional[bool],
        template: Optional[AzureTemplate] = None,
        request_context: Optional[CycleCloudRequestContext] = None,
    ) -> AsyncCycleCloudSessionContext:
        provider_cfg = self._get_provider_cyclecloud_config()
        token_provider = None
        try:
            credential = await self.azure_client.get_async_credential()
        except AuthenticationError:
            credential = None
        if credential is not None:
            token_provider = AsyncAzureCredentialAccessTokenProvider(credential)
        session_builder = CycleCloudSessionBuilder(
            cc_url=cc_url,
            verify_ssl=verify_ssl,
            template=template,
            request_context=request_context,
            provider_cfg=provider_cfg,
            async_token_provider=token_provider,
        )
        settings = session_builder.build_settings()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        auth_headers, auth, resolved_auth_mode = await session_builder.resolve_async_auth(
            settings=settings,
        )
        headers.update(auth_headers)
        connect_timeout, read_timeout = self._get_cc_request_timeout()
        client = httpx.AsyncClient(
            verify=settings.verify_ssl,
            headers=headers,
            auth=auth,
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=read_timeout,
                pool=connect_timeout,
            ),
        )
        return AsyncCycleCloudSessionContext(
            client=client,
            base_url=settings.base_url,
            auth_mode=resolved_auth_mode,
            credential_path=settings.credential_path,
            verify_ssl=settings.verify_ssl,
        )

    @asynccontextmanager
    async def _async_cc_session_scope(
        self,
        *,
        cc_url: Optional[str],
        verify_ssl: Optional[bool],
        template: Optional[AzureTemplate] = None,
        request_context: Optional[CycleCloudRequestContext] = None,
    ):
        session_context = await self._build_async_cc_session(
            cc_url=cc_url,
            verify_ssl=verify_ssl,
            template=template,
            request_context=request_context,
        )
        try:
            yield session_context
        finally:
            await session_context.client.aclose()

    async def _cc_request_async(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        include_metadata: bool = False,
        **kwargs: Any,
    ) -> Any:
        try:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            body = _response_json_or_error(
                response=response,
                url=url,
                decode_error_types=(json.JSONDecodeError,),
            )
            if not include_metadata:
                return body
            return _response_metadata(response, url=str(response.request.url)) | {"body": body}
        except httpx.ConnectError as exc:
            raise CycleCloudConnectionError(
                f"Cannot connect to CycleCloud at {url}: {exc}",
                url=url,
            ) from exc
        except httpx.HTTPStatusError as exc:
            response = exc.response
            body = response.text
            body_json: dict[str, Any] | None = None
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    body_json = parsed
            except json.JSONDecodeError:
                body_json = None

            raise CycleCloudConnectionError(
                f"CycleCloud API error (HTTP {response.status_code}): {body}",
                url=url,
                details={
                    "status_code": response.status_code,
                    "body": body,
                    **_http_error_details(body_json),
                },
            ) from exc
        except httpx.HTTPError as exc:
            raise CycleCloudConnectionError(
                f"CycleCloud API request failed: {exc}",
                url=url,
            ) from exc

    async def _resolve_release_node_targets_via_fetch_async(
        self,
        *,
        fetch_nodes: Callable[[], Awaitable[dict[str, Any]]],
        machine_ids: list[str],
    ) -> dict[str, list[str]]:
        """Async variant of release-target resolution using fetched cluster nodes."""
        try:
            nodes_response = await fetch_nodes()
        except CycleCloudConnectionError:
            return {"names": machine_ids}
        return self._resolve_release_node_targets_from_nodes(
            nodes=nodes_response.get("nodes", []),
            machine_ids=machine_ids,
        )

    async def _resolve_release_node_targets_async(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        cluster_name: str,
        machine_ids: list[str],
    ) -> dict[str, list[str]]:
        """Resolve stored machine IDs to the strongest CycleCloud identifier set available."""
        return await self._resolve_release_node_targets_via_fetch_async(
            fetch_nodes=lambda: self._cc_request_async(
                client,
                "GET",
                f"{base_url}/clusters/{cluster_name}/nodes",
            ),
            machine_ids=machine_ids,
        )

    def _resolve_release_node_targets_from_nodes(
        self,
        *,
        nodes: list[dict[str, Any]],
        machine_ids: list[str],
    ) -> dict[str, list[str]]:
        """Resolve stored machine IDs using fetched CycleCloud node payloads."""
        resolved_ids: list[str] = []
        resolved_names: list[str] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()

        for machine_id in machine_ids:
            matched = False
            for node in nodes:
                parsed_node = _parse_cyclecloud_node(node)
                if machine_id in {parsed_node.name, parsed_node.node_id}:
                    if parsed_node.node_id and parsed_node.node_id not in seen_ids:
                        resolved_ids.append(parsed_node.node_id)
                        seen_ids.add(parsed_node.node_id)
                    if parsed_node.name and parsed_node.name not in seen_names:
                        resolved_names.append(parsed_node.name)
                        seen_names.add(parsed_node.name)
                    matched = True
                    break
            if not matched and machine_id and machine_id not in seen_names:
                resolved_names.append(machine_id)
                seen_names.add(machine_id)

        if resolved_ids or resolved_names != machine_ids:
            self._logger.info(
                "Resolved CycleCloud release ids %s -> node_ids=%s node_names=%s",
                machine_ids,
                resolved_ids,
                resolved_names,
            )

        if resolved_ids:
            return {"ids": resolved_ids}
        return {"names": resolved_names}

    @staticmethod
    def _build_release_result(
        *,
        cluster_name: str,
        terminate_response: dict[str, Any],
    ) -> AzureReleaseHostsResult:
        """Build the normalized CycleCloud release submission result."""
        return {
            "provider_data": {
                "cluster_name": cluster_name,
                "terminate_operation_location": (
                    terminate_response.get("headers", {}).get("Location")
                ),
                "operation_status": "submitted",
            }
        }

    # ------------------------------------------------------------------
    # acquire_hosts
    # ------------------------------------------------------------------

    def _prepare_acquire_request(
        self,
        *,
        request: Request,
        template: AzureTemplate,
    ) -> _CycleCloudAcquireRequest:
        """Build the CycleCloud create-nodes request payload."""
        cluster_name = template.cluster_name
        node_array = template.node_array
        vm_size = template.vm_size
        count = request.requested_count

        if not cluster_name:
            raise CycleCloudNodeError(
                "cluster_name is required for CycleCloud provisioning.",
                cluster_name=cluster_name or "",
                node_array=node_array,
            )

        definition: dict[str, Any] = {}
        if not template.uses_vm_size_mix:
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

        if template.node_attributes:
            node_params["sets"][0]["definition"].update(template.node_attributes)

        subnet_id = self._resolve_subnet_id(template)
        if subnet_id:
            node_params["sets"][0]["definition"]["SubnetId"] = subnet_id

        return _CycleCloudAcquireRequest(
            cluster_name=cluster_name,
            node_array=node_array,
            vm_size=vm_size,
            count=count,
            cyclecloud_request_id=cyclecloud_request_id,
            node_params=node_params,
        )

    async def _validate_cluster_exists_async(
        self,
        *,
        cluster_name: str,
        fetch_cluster_status: Callable[[], Awaitable[dict[str, Any]]],
    ) -> None:
        """Async variant of CycleCloud cluster existence validation."""
        try:
            self._log_cluster_state(
                cluster_name=cluster_name,
                cluster_status=await fetch_cluster_status(),
            )
        except CycleCloudConnectionError as exc:
            if exc.details and exc.details.get("status_code") == 404:
                raise CycleCloudClusterNotFoundError(
                    f"CycleCloud cluster '{cluster_name}' not found.",
                    cluster_name=cluster_name,
                ) from exc
            raise

    def _log_cluster_state(self, *, cluster_name: str, cluster_status: dict[str, Any]) -> None:
        """Log the current CycleCloud cluster state after a successful status fetch."""
        cluster_state = cluster_status.get("state", "Unknown")
        self._logger.debug("CycleCloud cluster '%s' state: %s", cluster_name, cluster_state)

    def _build_acquire_result(
        self,
        *,
        request_data: _CycleCloudAcquireRequest,
        template: AzureTemplate,
        base_url: str,
        credential_path: Optional[str],
        verify_ssl: bool,
        auth_mode: Optional[str],
        create_response: dict[str, Any],
    ) -> AzureAcquireHostsResult:
        """Build the normalized CycleCloud acquire response payload."""
        result = create_response.get("body") or {}
        operation_id = result.get("operationId", "")
        operation_location = create_response.get("headers", {}).get("Location")
        created_sets = result.get("sets", [])
        fleet_errors: list[ProviderErrorEntry] = []
        added_count = 0

        for node_set in created_sets:
            added = node_set.get("added", 0)
            added_count += int(added or 0)
            for node in node_set.get("nodes", []):
                node_errors = _extract_cyclecloud_node_errors(
                    node,
                    cluster_name=request_data.cluster_name,
                    node_array=request_data.node_array,
                )
                for error in node_errors:
                    if error not in fleet_errors:
                        fleet_errors.append(error)

        self._logger.info(
            "CycleCloud node request accepted for cluster '%s': operation_id=%s, added=%d",
            request_data.cluster_name,
            operation_id,
            added_count,
        )

        return {
            "success": True,
            "resource_ids": [request_data.cyclecloud_request_id],
            "instances": [],
            "error_message": None,
            "provider_data": {
                "cluster_name": request_data.cluster_name,
                "node_array": request_data.node_array,
                "operation_id": operation_id,
                "operation_location": operation_location,
                "added_count": added_count,
                "submitted_count": request_data.count,
                "operation_status": "submitted",
                "fulfillment_final": True,
                "resource_group": template.resource_group.value,
                "location": template.location.value,
                "error_codes": collect_provider_error_codes(fleet_errors),
                "fleet_errors": fleet_errors,
                "cyclecloud_url": base_url,
                "cyclecloud_credential_path": credential_path,
                "cyclecloud_verify_ssl": verify_ssl,
                "cyclecloud_auth_mode": auth_mode,
                "cyclecloud_aad_scope": template.cyclecloud_aad_scope,
            },
        }

    def _prepare_status_request(self, request: Request) -> Optional[_CycleCloudStatusRequest]:
        """Validate and normalize the durable status-check request context."""
        resource_ids = request.resource_ids
        if not resource_ids:
            self._logger.warning("check_hosts_status called with no resource_ids")
            return None

        request_context = CycleCloudRequestContext.from_mapping(request.metadata or {})
        cluster_name = request_context.cluster_name
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
            message = f"CycleCloud request identity is required for status check in cluster '{cluster_name}'"
            self._logger.error(message)
            raise CycleCloudConnectionError(
                message,
                url=request_context.cyclecloud_url,
                details={"resource_ids": resource_ids},
            )

        return _CycleCloudStatusRequest(
            request_context=request_context,
            cluster_name=cluster_name,
            node_array=request_context.node_array,
            node_ids=list(request_context.node_ids),
            cyclecloud_request_id=cyclecloud_request_id,
        )

    def _log_status_check_failure(
        self,
        *,
        cluster_name: str,
        cyclecloud_request_id: str,
        exc: CycleCloudConnectionError,
    ) -> None:
        """Normalize logging for status-check failures across sync and async paths."""
        if "Cannot connect to CycleCloud" in str(exc):
            self._logger.error(
                "Failed to build CycleCloud session for status check (cluster '%s'): %s",
                cluster_name,
                exc,
            )
            return
        self._logger.error(
            "Failed to get node status for cluster '%s' and request_id '%s': %s",
            cluster_name,
            cyclecloud_request_id,
            exc,
        )

    def _build_status_results(
        self,
        *,
        status_request: _CycleCloudStatusRequest,
        nodes_response: dict[str, Any],
    ) -> list[AzureHandlerStatusResult]:
        """Build normalized status results from a CycleCloud nodes response."""
        results = _collect_cyclecloud_status_results(
            logger=self._logger,
            cluster_name=status_request.cluster_name,
            nodes=nodes_response.get("nodes", []),
            node_array=status_request.node_array,
            node_ids=status_request.node_ids,
        )

        self._logger.debug(
            "CycleCloud status check for cluster '%s': %d node(s) found",
            status_request.cluster_name,
            len(results),
        )
        return results

    async def _submit_release_request_async(
        self,
        *,
        cluster_name: str,
        machine_ids: list[str],
        resolve_node_targets: Callable[[], Awaitable[dict[str, list[str]]]],
        submit_terminate: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> AzureReleaseHostsResult:
        """Async variant of CycleCloud node termination submission."""
        self._logger.info(
            "Terminating %d node(s) from CycleCloud cluster '%s': %s",
            len(machine_ids),
            cluster_name,
            machine_ids,
        )
        return await self._submit_release_request_result_async(
            cluster_name=cluster_name,
            machine_ids=machine_ids,
            node_targets=await resolve_node_targets(),
            submit_terminate=submit_terminate,
        )

    async def _submit_release_request_result_async(
        self,
        *,
        cluster_name: str,
        machine_ids: list[str],
        node_targets: dict[str, list[str]],
        submit_terminate: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> AzureReleaseHostsResult:
        """Submit a prepared CycleCloud termination payload through the async client."""
        try:
            terminate_payload: dict[str, Any] = dict(node_targets)
            terminate_response = await submit_terminate(terminate_payload)
            self._logger.debug("Terminate request sent for CycleCloud nodes: %s", terminate_payload)
            self._logger.info(
                "Successfully submitted termination for %d node(s) from cluster '%s'",
                len(machine_ids),
                cluster_name,
            )
            return self._build_release_result(
                cluster_name=cluster_name,
                terminate_response=terminate_response,
            )
        except CycleCloudConnectionError as exc:
            raise TerminationError(
                f"Failed to terminate nodes from CycleCloud cluster '{cluster_name}': {exc}",
                resource_ids=machine_ids,
            ) from exc

    async def acquire_hosts_async(
        self, request: Request, template: AzureTemplate
    ) -> AzureAcquireHostsResult:
        """Async variant of ``acquire_hosts`` using ``httpx.AsyncClient``."""
        acquire_request = self._prepare_acquire_request(request=request, template=template)

        self._logger.info(
            "Adding %d node(s) to CycleCloud cluster '%s' node array '%s' (vm_size=%s)",
            acquire_request.count,
            acquire_request.cluster_name,
            acquire_request.node_array,
            acquire_request.vm_size,
        )

        async with self._async_cc_session_scope(
            cc_url=template.cyclecloud_url,
            verify_ssl=template.cyclecloud_verify_ssl,
            template=template,
        ) as session_context:
            client = session_context.client
            base_url = session_context.base_url

            await self._validate_cluster_exists_async(
                cluster_name=acquire_request.cluster_name,
                fetch_cluster_status=lambda: self._cc_request_async(
                    client,
                    "GET",
                    f"{base_url}/clusters/{acquire_request.cluster_name}/status",
                ),
            )

            try:
                create_response = await self._cc_request_async(
                    client,
                    "POST",
                    f"{base_url}/clusters/{acquire_request.cluster_name}/nodes/create",
                    include_metadata=True,
                    json=acquire_request.node_params,
                )
            except CycleCloudConnectionError as exc:
                raise CycleCloudNodeError(
                    f"Failed to add nodes to cluster '{acquire_request.cluster_name}': {exc}",
                    cluster_name=acquire_request.cluster_name,
                    node_array=acquire_request.node_array,
                ) from exc

        return self._build_acquire_result(
            request_data=acquire_request,
            template=template,
            base_url=base_url,
            credential_path=session_context.credential_path,
            verify_ssl=session_context.verify_ssl,
            auth_mode=session_context.auth_mode,
            create_response=create_response,
        )

    async def check_hosts_status_async(self, request: Request) -> list[AzureHandlerStatusResult]:
        """Async variant of ``check_hosts_status`` using ``httpx.AsyncClient``."""
        status_request = self._prepare_status_request(request)
        if status_request is None:
            return []

        try:
            async with self._async_cc_session_scope(
                cc_url=status_request.request_context.cyclecloud_url,
                verify_ssl=status_request.request_context.cyclecloud_verify_ssl,
                request_context=status_request.request_context,
            ) as session_context:
                nodes_response = await self._cc_request_async(
                    session_context.client,
                    "GET",
                    f"{session_context.base_url}/clusters/{status_request.cluster_name}/nodes",
                    params={"request_id": status_request.cyclecloud_request_id},
                )
        except CycleCloudConnectionError as exc:
            self._log_status_check_failure(
                cluster_name=status_request.cluster_name,
                cyclecloud_request_id=status_request.cyclecloud_request_id,
                exc=exc,
            )
            raise

        return self._build_status_results(
            status_request=status_request,
            nodes_response=nodes_response,
        )

    async def release_hosts_async(
        self,
        machine_ids: list[str],
        resource_id: str,
        context: Optional[AzureReleaseContext] = None,
    ) -> Optional[AzureReleaseHostsResult]:
        """Async variant of ``release_hosts`` using ``httpx.AsyncClient``."""
        release_context = context or AzureReleaseContext()
        request_context = release_context.cyclecloud_request_context
        cluster_name = str(request_context.cluster_name or resource_id)

        try:
            async with self._async_cc_session_scope(
                cc_url=request_context.cyclecloud_url,
                verify_ssl=request_context.cyclecloud_verify_ssl,
                request_context=request_context,
            ) as session_context:
                return await self._submit_release_request_async(
                    cluster_name=cluster_name,
                    machine_ids=machine_ids,
                    resolve_node_targets=lambda: self._resolve_release_node_targets_async(
                        client=session_context.client,
                        base_url=session_context.base_url,
                        cluster_name=cluster_name,
                        machine_ids=machine_ids,
                    ),
                    submit_terminate=lambda terminate_payload: self._cc_request_async(
                        session_context.client,
                        "POST",
                        f"{session_context.base_url}/clusters/{cluster_name}/nodes/terminate",
                        include_metadata=True,
                        json=terminate_payload,
                    ),
                )
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
        """Return example CycleCloud template configurations."""
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
