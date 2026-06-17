"""Azure ARM deployment helpers."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.infrastructure.azure_client import AzureClient


class AzureDeploymentService:
    """Build and submit Azure ARM deployments for provider-owned resources."""

    _TEMPLATE_SCHEMA = (
        "https://schema.management.azure.com/schemas/2019-04-01/"
        "deploymentTemplate.json#"
    )
    _DEPLOYMENT_API_VERSION = "2025-04-01"
    _DEPLOYMENT_PROVIDER_NAMESPACE = "Microsoft.Resources"
    _DEPLOYMENT_RESOURCE_TYPE = "deployments"
    _DEPLOYMENT_MODE = "Incremental"
    _VM_API_VERSION = "2023-09-01"
    _NETWORK_API_VERSION = "2023-09-01"

    def __init__(self, azure_client: AzureClient, logger: LoggingPort) -> None:
        """Initialize with an Azure client and logger."""
        self.azure_client = azure_client
        self._logger = logger

    @classmethod
    def build_deployment_name(cls, *parts: str) -> str:
        """Build a deterministic Azure deployment name within ARM limits."""
        normalized = [
            re.sub(r"[^A-Za-z0-9._()-]+", "-", str(part)).strip("-")
            for part in parts
            if str(part).strip()
        ]
        name = "-".join(part for part in normalized if part) or "orb-deployment"
        if len(name) <= 64:
            return name

        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        prefix = name[:55].rstrip("-")
        return f"{prefix}-{digest}"

    @staticmethod
    def resource_id_expression(resource_type: str, resource_name: str) -> str:
        """Return an ARM expression for a sibling resource ID."""
        return f"[resourceId('{resource_type}', '{resource_name}')]"

    async def submit_template_deployment_async(
        self,
        *,
        resource_group: str,
        deployment_name: str,
        template: dict[str, Any],
        parameters: Optional[dict[str, Any]] = None,
    ) -> str:
        """Submit an ARM deployment without waiting for completion via the async SDK."""
        deployment_payload = {
            "properties": {
                "mode": self._DEPLOYMENT_MODE,
                "template": template,
                "parameters": parameters or {},
            }
        }
        resource_client = await self.azure_client.get_async_resource_client()
        resource_operations: Any = resource_client.resources
        await resource_operations.begin_create_or_update(
            resource_group_name=resource_group,
            resource_provider_namespace=self._DEPLOYMENT_PROVIDER_NAMESPACE,
            parent_resource_path="",
            resource_type=self._DEPLOYMENT_RESOURCE_TYPE,
            resource_name=deployment_name,
            api_version=self._DEPLOYMENT_API_VERSION,
            parameters=deployment_payload,
        )

        self._logger.info(
            "Submitted ARM deployment '%s' in resource group '%s'",
            deployment_name,
            resource_group,
        )
        return deployment_name

    async def get_deployment_status_async(
        self,
        *,
        resource_group: str,
        deployment_name: str,
    ) -> Optional[dict[str, Any]]:
        """Return provisioning metadata for a submitted ARM deployment via the async SDK."""
        resource_client = await self.azure_client.get_async_resource_client()
        deployment = await resource_client.resources.get(
            resource_group_name=resource_group,
            resource_provider_namespace=self._DEPLOYMENT_PROVIDER_NAMESPACE,
            parent_resource_path="",
            resource_type=self._DEPLOYMENT_RESOURCE_TYPE,
            resource_name=deployment_name,
            api_version=self._DEPLOYMENT_API_VERSION,
        )
        properties = deployment.properties
        if not isinstance(properties, dict):
            properties = {}

        error = properties.get("error")
        error_code = None
        error_message = None
        if isinstance(error, dict):
            error_code = error.get("code")
            error_message = error.get("message")

        return {
            "provisioning_state": properties.get("provisioningState"),
            "error_code": error_code,
            "error_message": error_message,
        }

    def build_single_vm_deployment_template(
        self,
        *,
        location: str,
        subnet_id: str,
        vm_definitions: list[dict[str, Any]],
        enable_accelerated_networking: bool = False,
        nsg_id: Optional[str] = None,
        load_balancer_backend_pool_ids: Optional[list[str]] = None,
        load_balancer_inbound_nat_pool_ids: Optional[list[str]] = None,
        application_gateway_backend_pool_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Build a deployment template containing Public IP, NIC, and VM resources."""
        resources: list[dict[str, Any]] = []
        for vm_definition in vm_definitions:
            vm_name = vm_definition["vm_name"]
            nic_name = vm_definition["nic_name"]
            vm_payload = vm_definition["vm_payload"]
            public_ip_name = vm_definition.get("public_ip_name")

            nic_depends_on: list[str] = []
            ip_config_properties: dict[str, Any] = {
                "subnet": {"id": subnet_id},
                "privateIPAllocationMethod": "Dynamic",
            }

            if public_ip_name:
                public_ip_id = self.resource_id_expression(
                    "Microsoft.Network/publicIPAddresses",
                    public_ip_name,
                )
                resources.append({
                    "type": "Microsoft.Network/publicIPAddresses",
                    "apiVersion": self._NETWORK_API_VERSION,
                    "name": public_ip_name,
                    "location": location,
                    "sku": {"name": "Standard"},
                    "properties": {
                        "publicIPAllocationMethod": "Static",
                        "deleteOption": "Delete",
                    },
                })
                nic_depends_on.append(public_ip_id)
                ip_config_properties["publicIPAddress"] = {
                    "id": public_ip_id,
                    "deleteOption": "Delete",
                }

            if load_balancer_backend_pool_ids:
                ip_config_properties["loadBalancerBackendAddressPools"] = [
                    {"id": pool_id} for pool_id in load_balancer_backend_pool_ids
                ]
            if load_balancer_inbound_nat_pool_ids:
                ip_config_properties["loadBalancerInboundNatPools"] = [
                    {"id": pool_id} for pool_id in load_balancer_inbound_nat_pool_ids
                ]
            if application_gateway_backend_pool_ids:
                ip_config_properties["applicationGatewayBackendAddressPools"] = [
                    {"id": pool_id} for pool_id in application_gateway_backend_pool_ids
                ]

            nic_resource: dict[str, Any] = {
                "type": "Microsoft.Network/networkInterfaces",
                "apiVersion": self._NETWORK_API_VERSION,
                "name": nic_name,
                "location": location,
                "properties": {
                    "ipConfigurations": [
                        {
                            "name": "ipconfig1",
                            "properties": ip_config_properties,
                        }
                    ],
                    "enableAcceleratedNetworking": enable_accelerated_networking,
                },
            }
            if nsg_id:
                nic_resource["properties"]["networkSecurityGroup"] = {"id": nsg_id}
            if nic_depends_on:
                nic_resource["dependsOn"] = nic_depends_on
            resources.append(nic_resource)

            vm_resource_payload = {
                key: value
                for key, value in vm_payload.items()
                if key not in {"type", "apiVersion", "name", "dependsOn"}
            }
            vm_resource = dict(vm_resource_payload)
            vm_resource["type"] = "Microsoft.Compute/virtualMachines"
            vm_resource["apiVersion"] = self._VM_API_VERSION
            vm_resource["name"] = vm_name
            vm_resource["dependsOn"] = [
                self.resource_id_expression("Microsoft.Network/networkInterfaces", nic_name)
            ]
            resources.append(vm_resource)

        return {
            "$schema": self._TEMPLATE_SCHEMA,
            "contentVersion": "1.0.0.0",
            "resources": resources,
        }
