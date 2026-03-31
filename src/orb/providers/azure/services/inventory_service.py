"""Azure status and resource-discovery orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.infrastructure.azure_client import AzureClient
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureHandler
from orb.providers.base.strategy import ProviderOperation, ProviderResult


@dataclass
class AzureStatusQueryContext:
    instance_ids: list[str]
    resource_group: str
    provider_api: Optional[AzureProviderApi | str]


class AzureInventoryService:
    """Own Azure status querying and resource discovery orchestration."""

    def __init__(self, logger: LoggingPort) -> None:
        self._logger = logger

    @staticmethod
    def request_metadata(operation: ProviderOperation) -> dict[str, Any]:
        return dict(operation.parameters.get("request_metadata") or {})

    @staticmethod
    def resolve_operation_resource_group(
        operation: ProviderOperation,
        default_resource_group: Optional[str],
    ) -> Optional[str]:
        request_metadata = AzureInventoryService.request_metadata(operation)
        request_resource_group = request_metadata.get("resource_group")
        if request_resource_group not in (None, ""):
            return str(request_resource_group)
        return default_resource_group

    @staticmethod
    def group_instance_ids_by_resource(
        instance_ids: list[str],
        resource_mapping: dict[str, Any],
    ) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        if not resource_mapping:
            return grouped

        for key, value in resource_mapping.items():
            resource_id: Optional[str] = None
            mapped_ids: list[str] = []

            if isinstance(value, tuple):
                if value:
                    resource_id = value[0] if isinstance(value[0], str) else None
                mapped_ids = [key]
            elif isinstance(value, str):
                resource_id = value
                mapped_ids = [key]
            elif isinstance(value, list):
                if isinstance(key, str):
                    resource_id = key
                mapped_ids = [str(v) for v in value if v]

            if not resource_id:
                continue

            bucket = grouped.setdefault(resource_id, [])
            for mapped_id in mapped_ids:
                if mapped_id in instance_ids and mapped_id not in bucket:
                    bucket.append(mapped_id)

        return grouped

    @staticmethod
    def cyclecloud_metadata_keys() -> tuple[str, ...]:
        return (
            "cluster_name",
            "node_array",
            "node_ids",
            "operation_id",
            "operation_location",
            "cyclecloud_url",
            "cyclecloud_credential_path",
            "cyclecloud_verify_ssl",
            "cyclecloud_auth_mode",
            "cyclecloud_aad_scope",
        )

    def build_cyclecloud_request_metadata(
        self,
        *,
        operation: ProviderOperation,
        resource_group: Optional[str],
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {"resource_group": resource_group}
        request_metadata = self.request_metadata(operation)
        for key in self.cyclecloud_metadata_keys():
            value = request_metadata.get(key)
            if value not in (None, ""):
                metadata[key] = value
        return metadata

    @staticmethod
    def status_candidate_ids(result: dict[str, Any]) -> set[str]:
        provider_data = result.get("provider_data") or {}
        candidate_ids = {
            str(result.get("instance_id")),
            str(provider_data.get("vm_id")),
            str(provider_data.get("vmss_instance_id")),
            str(provider_data.get("node_id")),
            str(provider_data.get("vm_name")),
        }
        candidate_ids.discard("None")
        candidate_ids.discard("")
        return candidate_ids

    def observed_status_ids(self, instance_details: list[dict[str, Any]]) -> set[str]:
        observed_ids: set[str] = set()
        for instance in instance_details:
            observed_ids.update(self.status_candidate_ids(instance))
        return observed_ids

    @staticmethod
    def filter_status_results(
        results: list[dict[str, Any]],
        requested_ids: list[str],
    ) -> list[dict[str, Any]]:
        requested = {str(item) for item in requested_ids}
        filtered: list[dict[str, Any]] = []
        for result in results:
            provider_data = result.get("provider_data") or {}
            candidate_ids = {
                str(result.get("instance_id")),
                str(provider_data.get("vm_id")),
                str(provider_data.get("vmss_instance_id")),
                str(provider_data.get("node_id")),
                str(provider_data.get("vm_name")),
            }
            candidate_ids.discard("None")
            if candidate_ids & requested:
                filtered.append(result)
        return filtered

    def status_resource_ids(
        self,
        operation: ProviderOperation,
        instance_ids: list[str],
    ) -> list[str]:
        resource_ids: list[str] = []
        raw_resource_mapping = operation.parameters.get("resource_mapping", {}) or {}
        for resource_id, mapped_ids in self.group_instance_ids_by_resource(
            instance_ids,
            raw_resource_mapping,
        ).items():
            if resource_id and mapped_ids and resource_id not in resource_ids:
                resource_ids.append(resource_id)

        direct_resource_id = operation.parameters.get("resource_id")
        if direct_resource_id not in (None, "") and str(direct_resource_id) not in resource_ids:
            resource_ids.append(str(direct_resource_id))
        return resource_ids

    def status_dry_run_result(self, instance_ids: list[str]) -> ProviderResult:
        return ProviderResult.success_result(
            {
                "instances": [
                    {
                        "instance_id": instance_id,
                        "status": "unknown",
                        "provider_type": "azure",
                        "provider_data": {"dry_run": True},
                    }
                    for instance_id in instance_ids
                ],
                "queried_count": len(instance_ids),
            },
            {
                "operation": "get_instance_status",
                "instance_ids": instance_ids,
                "method": "dry_run",
                "provider_data": {"dry_run": True},
            },
        )

    def sdk_status_result(
        self,
        *,
        status_context: AzureStatusQueryContext,
        azure_client: Optional[AzureClient],
        machine_conversion_service: Any,
    ) -> ProviderResult:
        if not azure_client:
            return ProviderResult.error_result(
                "Azure client not available", "AZURE_CLIENT_NOT_AVAILABLE"
            )

        machines: list[dict[str, Any]] = []
        compute = azure_client.compute_client

        for vm_id in status_context.instance_ids:
            try:
                vm = compute.virtual_machines.get(
                    resource_group_name=status_context.resource_group,
                    vm_name=vm_id,
                    expand="instanceView",
                )
                machines.append(machine_conversion_service.convert_sdk_vm(vm, azure_client))
            except Exception as exc:
                self._logger.error("Failed to get status for VM '%s': %s", vm_id, exc)
                machines.append(
                    {
                        "instance_id": vm_id,
                        "status": "unknown",
                        "provider_type": "azure",
                        "error": str(exc),
                    }
                )

        return ProviderResult.success_result(
            {"instances": machines, "queried_count": len(status_context.instance_ids)},
            {"operation": "get_instance_status", "instance_ids": status_context.instance_ids},
        )

    def status_handler_result(
        self,
        *,
        operation: ProviderOperation,
        status_context: AzureStatusQueryContext,
        handler_machines: list[dict[str, Any]],
    ) -> ProviderResult:
        metadata = {
            "operation": "get_instance_status",
            "instance_ids": status_context.instance_ids,
            "method": "handler",
        }

        return ProviderResult.success_result(
            {
                "instances": handler_machines,
                "queried_count": len(status_context.instance_ids),
            },
            metadata,
        )

    def _collect_grouped_status(
        self,
        *,
        grouped_resource_mapping: dict[str, list[str]],
        handler: Optional[AzureHandler],
        provider_api_value: AzureProviderApi | str,
        build_metadata: Callable[[Optional[dict[str, Any]]], dict[str, Any]],
        make_request: Callable[[list[str], dict[str, Any]], Any],
        provider_api_key: Callable[[AzureProviderApi | str], str],
        handlers: dict[str, AzureHandler],
    ) -> list[dict[str, Any]]:
        all_results: list[dict[str, Any]] = []
        seen_instance_ids: set[str] = set()

        for resource_id, mapped_ids in grouped_resource_mapping.items():
            group_handler = handler
            if not group_handler and provider_api_value:
                group_handler = handlers.get(provider_api_key(provider_api_value))
            if not group_handler:
                continue

            extra_metadata: dict[str, Any] = {}
            if provider_api_value == AzureProviderApi.CYCLECLOUD:
                extra_metadata["node_ids"] = mapped_ids
            request = make_request([resource_id], build_metadata(extra_metadata))
            for machine in self.filter_status_results(
                group_handler.check_hosts_status(request), mapped_ids
            ):
                machine_id = str(machine.get("instance_id"))
                if machine_id not in seen_instance_ids:
                    all_results.append(machine)
                    seen_instance_ids.add(machine_id)

        return all_results

    def get_instance_status_via_handlers(
        self,
        *,
        operation: ProviderOperation,
        instance_ids: list[str],
        resource_group: str,
        provider_instance_name: str,
        resolve_operation_provider_api: Callable[[ProviderOperation], Optional[AzureProviderApi | str]],
        provider_api_key: Callable[[AzureProviderApi | str], str],
        handlers: dict[str, AzureHandler],
    ) -> Optional[list[dict[str, Any]]]:
        provider_api = resolve_operation_provider_api(operation)
        raw_resource_mapping = operation.parameters.get("resource_mapping", {}) or {}
        grouped_resource_mapping = self.group_instance_ids_by_resource(
            instance_ids, raw_resource_mapping
        )

        if not provider_api:
            return None

        handler = handlers.get(provider_api_key(provider_api))
        if not handler and provider_api == AzureProviderApi.VMSS_UNIFORM:
            handler = handlers.get(AzureProviderApi.VMSS.value)
        if not handler and not grouped_resource_mapping:
            return None

        from orb.domain.request.aggregate import Request
        from orb.domain.request.value_objects import RequestType

        request_id = operation.parameters.get("request_id") or (
            operation.context.get("request_id") if operation.context else None
        )

        def build_metadata(additional: Optional[dict[str, Any]] = None) -> dict[str, Any]:
            metadata = self.build_cyclecloud_request_metadata(
                operation=operation,
                resource_group=resource_group,
            )
            if additional:
                metadata.update(additional)
            return metadata

        def make_request(resource_ids: list[str], metadata: dict[str, Any]) -> Request:
            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id=operation.parameters.get("template_id", "unknown"),
                machine_count=1,
                provider_type="azure",
                provider_name=provider_instance_name,
                request_id=request_id,
                metadata=metadata,
            )
            request.resource_ids = resource_ids
            return request

        if provider_api == AzureProviderApi.SINGLE_VM and handler:
            request = make_request(instance_ids, build_metadata())
            return handler.check_hosts_status(request)

        if grouped_resource_mapping:
            results = self._collect_grouped_status(
                grouped_resource_mapping=grouped_resource_mapping,
                handler=handler,
                provider_api_value=provider_api,
                build_metadata=build_metadata,
                make_request=make_request,
                provider_api_key=provider_api_key,
                handlers=handlers,
            )
            if results:
                return results

        resource_id = operation.parameters.get("resource_id")
        if not handler or not resource_id:
            return None

        extra_metadata: dict[str, Any] = {}
        if provider_api == AzureProviderApi.CYCLECLOUD:
            extra_metadata = {"node_ids": instance_ids}
        request = make_request(
            instance_ids if provider_api == AzureProviderApi.SINGLE_VM else [resource_id],
            build_metadata(extra_metadata),
        )
        if provider_api == AzureProviderApi.SINGLE_VM:
            return handler.check_hosts_status(request)
        return self.filter_status_results(handler.check_hosts_status(request), instance_ids)

    def describe_resource_instances(
        self,
        *,
        operation: ProviderOperation,
        handlers: dict[str, AzureHandler],
        provider_instance_name: str,
        provider_api: AzureProviderApi | str,
        provider_api_key: str,
        resource_group: Optional[str],
        resource_manager: Any,
        deployment_service: Any,
        resource_metadata_service: Any,
        fail_on_partial_status_error: bool = False,
    ) -> ProviderResult:
        resource_ids = operation.parameters.get("resource_ids", [])
        handler = handlers.get(provider_api_key)
        if not handler:
            return ProviderResult.error_result(
                f"No handler available for provider_api: {provider_api_key}",
                "HANDLER_NOT_FOUND",
            )

        from orb.domain.request.aggregate import Request
        from orb.domain.request.value_objects import RequestType

        request_id = operation.parameters.get("request_id") or (
            operation.context.get("request_id") if operation.context else None
        )
        request_metadata = self.build_cyclecloud_request_metadata(
            operation=operation,
            resource_group=resource_group,
        )
        if provider_api == AzureProviderApi.SINGLE_VM:
            deployment_name = self.request_metadata(operation).get("deployment_name")
            if deployment_name not in (None, ""):
                request_metadata["deployment_name"] = str(deployment_name)
        if fail_on_partial_status_error:
            request_metadata["fail_on_partial_status_error"] = True

        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id=operation.parameters.get("template_id", "unknown"),
            machine_count=1,
            provider_type="azure",
            provider_name=provider_instance_name,
            request_id=request_id,
            metadata=request_metadata,
        )
        request.resource_ids = resource_ids

        instance_details = handler.check_hosts_status(request)

        if not instance_details:
            metadata = {
                "operation": "describe_resource_instances",
                "resource_ids": resource_ids,
                "provider_api": provider_api_key,
                "handler_used": provider_api_key,
                "instance_count": 0,
            }
            if provider_api in (AzureProviderApi.VMSS, AzureProviderApi.VMSS_UNIFORM):
                vmss_errors = []
                if resource_group and hasattr(handler, "get_vmss_resource_errors"):
                    for resource_id in resource_ids:
                        for error in handler.get_vmss_resource_errors(resource_group, resource_id):
                            if error not in vmss_errors:
                                vmss_errors.append(error)
                if vmss_errors:
                    metadata["fleet_errors"] = vmss_errors
                resource_metadata_service.augment_vmss_capacity_metadata(
                    metadata,
                    resource_ids,
                    resource_manager=resource_manager,
                    resource_group=resource_group,
                )
            elif provider_api == AzureProviderApi.SINGLE_VM:
                resource_metadata_service.augment_single_vm_deployment_metadata(
                    metadata,
                    request_metadata,
                    resource_group=resource_group,
                    deployment_service=deployment_service,
                )
            return ProviderResult.success_result({"instances": []}, metadata)

        fleet_errors: list[dict[str, Any]] = []
        for inst in instance_details:
            provider_data = inst.get("provider_data") or {}
            if isinstance(provider_data, dict):
                for error in provider_data.get("fleet_errors") or []:
                    if error not in fleet_errors:
                        fleet_errors.append(error)

        metadata: dict[str, Any] = {
            "operation": "describe_resource_instances",
            "resource_ids": resource_ids,
            "provider_api": provider_api_key,
            "handler_used": provider_api_key,
            "instance_count": len(instance_details),
        }
        if fleet_errors:
            metadata["fleet_errors"] = fleet_errors

        if provider_api in (AzureProviderApi.VMSS, AzureProviderApi.VMSS_UNIFORM):
            resource_metadata_service.augment_vmss_capacity_metadata(
                metadata,
                resource_ids,
                resource_manager=resource_manager,
                resource_group=resource_group,
            )

        resource_metadata_service.augment_shortfall_metadata(metadata)

        return ProviderResult.success_result(
            data={"instances": instance_details},
            metadata=metadata,
        )
