"""GCP provider strategy."""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType
from orb.providers.base.strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)
from orb.providers.gcp.capabilities import get_supported_api_capabilities, get_supported_apis
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.configuration.template_extension import GCPTemplateExtensionConfig
from orb.providers.gcp.configuration.validator import validate_gcp_template
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.domain.template.value_objects import GCPProviderApi
from orb.providers.gcp.exceptions import GCPError, translate_gcp_exception
from orb.providers.gcp.infrastructure import (
    GCPComputeClient,
    GCPHandlerFactory,
)
from orb.providers.gcp.services.health_check_service import GCPHealthCheckService
from orb.providers.gcp.types import GCPHandlerContext


@injectable
class GCPProviderStrategy(ProviderStrategy):
    """GCP implementation of the provider strategy interface."""

    def __init__(
        self,
        config: GCPProviderConfig,
        logger: LoggingPort,
        provider_name: Optional[str] = None,
    ) -> None:
        if not isinstance(config, GCPProviderConfig):
            raise ValueError("GCPProviderStrategy requires GCPProviderConfig")
        super().__init__(config)
        self._config = config
        self._logger = logger
        self._provider_name = provider_name
        self._health_service = GCPHealthCheckService(config=config, logger=logger)
        self._compute_client: Optional[GCPComputeClient] = None
        self._handler_factory: Optional[GCPHandlerFactory] = None

    @property
    def provider_type(self) -> str:
        return "gcp"

    @classmethod
    def get_defaults_config(cls) -> dict:
        defaults = GCPTemplateExtensionConfig().to_template_defaults()
        return {
            "provider": {
                "provider_defaults": {
                    "gcp": {
                        "template_defaults": defaults,
                    }
                },
            }
        }

    def initialize(self) -> bool:
        self._compute_client = GCPComputeClient(
            config=self._config,
            logger=self._logger,
        )
        self._handler_factory = GCPHandlerFactory(
            compute_client=self._compute_client,
            config=self._config,
            logger=self._logger,
        )
        self._initialized = True
        self._logger.info("GCP provider strategy ready for project: %s", self._config.project_id)
        return True

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        if not self._initialized:
            return ProviderResult.error_result(
                "GCP provider strategy not initialized", "NOT_INITIALIZED"
            )

        start_time = time.time()
        try:
            result = await self._execute_operation_internal(operation)
            if result.metadata is None:
                result.metadata = {}
            result.metadata.update(
                {
                    "execution_time_ms": int((time.time() - start_time) * 1000),
                    "provider": "gcp",
                }
            )
            return result
        except GCPError as exc:
            self._logger.error("GCP operation failed: %s", exc, exc_info=True)
            return ProviderResult.error_result(
                str(exc),
                exc.error_code,
                {
                    "execution_time_ms": int((time.time() - start_time) * 1000),
                    "provider": "gcp",
                    "details": exc.details,
                },
            )
        except Exception as exc:
            translated = translate_gcp_exception(
                exc,
                operation=operation.operation_type.value,
            )
            self._logger.error("GCP operation failed: %s", translated, exc_info=True)
            return ProviderResult.error_result(
                str(translated),
                translated.error_code,
                {
                    "execution_time_ms": int((time.time() - start_time) * 1000),
                    "provider": "gcp",
                    "details": translated.details,
                },
            )

    async def _execute_operation_internal(self, operation: ProviderOperation) -> ProviderResult:
        op = operation.operation_type
        if op == ProviderOperationType.CREATE_INSTANCES:
            return self._handle_create_instances(operation)
        if op == ProviderOperationType.TERMINATE_INSTANCES:
            return self._handle_terminate_instances(operation)
        if op == ProviderOperationType.GET_INSTANCE_STATUS:
            return self._handle_get_instance_status(operation)
        if op == ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES:
            return self._handle_describe_resource_instances(operation)
        if op == ProviderOperationType.VALIDATE_TEMPLATE:
            template_config = operation.parameters.get("template_config", {})
            result = validate_gcp_template(template_config)
            if result["valid"]:
                return ProviderResult.success_result(result, {"operation": "validate_template"})
            return ProviderResult.error_result(
                "; ".join(result["errors"]) or "Template validation failed",
                "VALIDATION_FAILED",
                result,
            )
        if op == ProviderOperationType.GET_AVAILABLE_TEMPLATES:
            return ProviderResult.success_result(
                GCPTemplateExtensionConfig().to_template_defaults(),
                {"operation": "get_available_templates"},
            )
        if op == ProviderOperationType.HEALTH_CHECK:
            health = self.check_health()
            return ProviderResult.success_result(
                {
                    "is_healthy": health.is_healthy,
                    "status_message": health.status_message,
                    "response_time_ms": health.response_time_ms,
                },
                {"operation": "health_check"},
            )
        if op == ProviderOperationType.RESOLVE_IMAGE:
            return self._handle_resolve_image(operation)
        if op == ProviderOperationType.START_INSTANCES:
            return self._handle_start_instances(operation)
        if op == ProviderOperationType.STOP_INSTANCES:
            return self._handle_stop_instances(operation)
        return ProviderResult.error_result(
            f"Unsupported operation: {operation.operation_type}", "UNSUPPORTED_OPERATION"
        )

    def _handle_create_instances(self, operation: ProviderOperation) -> ProviderResult:
        template_config = operation.parameters.get("template_config", {})
        count = int(operation.parameters.get("count", 1))
        if not template_config:
            return ProviderResult.error_result(
                "template_config is required for create_instances",
                "MISSING_TEMPLATE_CONFIG",
            )

        gcp_template = GCPTemplate.model_validate(self._build_gcp_template_config(template_config, count))
        handler = self._get_handler(gcp_template.provider_api)
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id=gcp_template.template_id,
            machine_count=count,
            provider_type="gcp",
            provider_name=self._provider_name,
            metadata=operation.parameters.get("request_metadata", {}),
            request_id=operation.parameters.get("request_id"),
        )
        request.provider_api = gcp_template.provider_api.value
        handler_result = handler.acquire_hosts(request, gcp_template)
        return ProviderResult.success_result(
            {
                "resource_ids": handler_result.get("resource_ids", []),
                "instances": handler_result.get("instances", []),
                "provider_api": gcp_template.provider_api.value,
                "count": count,
                "template_id": gcp_template.template_id,
            },
            {
                "operation": "create_instances",
                "handler_used": gcp_template.provider_api.value,
                "provider_data": handler_result.get("provider_data", {}),
            },
        )

    def _handle_terminate_instances(self, operation: ProviderOperation) -> ProviderResult:
        handler = self._get_handler_for_operation(operation)
        instance_ids = operation.parameters.get("instance_ids", []) or []
        resource_ids = operation.parameters.get("resource_ids", []) or []
        context = self._build_handler_context(operation)
        result = handler.terminate_hosts(
            resource_ids=resource_ids,
            instance_ids=instance_ids,
            context=context,
        )
        terminated_ids = result.get("terminated_ids", [])
        return ProviderResult.success_result(
            {"success": True, "terminated_count": len(terminated_ids)},
            {
                "operation": "terminate_instances",
                "instance_ids": instance_ids,
                "resource_ids": resource_ids,
                "provider_data": result,
            },
        )

    def _handle_get_instance_status(self, operation: ProviderOperation) -> ProviderResult:
        handler = self._get_handler_for_operation(operation)
        instances = handler.check_hosts_status(
            resource_ids=operation.parameters.get("resource_ids", []) or [],
            instance_ids=operation.parameters.get("instance_ids", []) or [],
            context=self._build_handler_context(operation),
        )
        return ProviderResult.success_result(
            {"instances": instances},
            {"operation": "get_instance_status"},
        )

    def _handle_describe_resource_instances(self, operation: ProviderOperation) -> ProviderResult:
        handler = self._get_handler_for_operation(operation)
        instances = handler.check_hosts_status(
            resource_ids=operation.parameters.get("resource_ids", []) or [],
            instance_ids=operation.parameters.get("instance_ids", []) or [],
            context=self._build_handler_context(operation),
        )
        return ProviderResult.success_result(
            {"instances": instances},
            {"operation": "describe_resource_instances"},
        )

    def _handle_resolve_image(self, operation: ProviderOperation) -> ProviderResult:
        family = operation.parameters.get("source_image_family")
        project = operation.parameters.get("source_image_project")
        if not family or not project:
            return ProviderResult.success_result({"resolved_images": {}})
        image = self._get_compute_client().get_image_from_family(image_project=project, family=family)
        return ProviderResult.success_result(
            {
                "resolved_images": {
                    "image_id": image.self_link or image.name,
                    "name": image.name,
                    "family": family,
                    "project": project,
                }
            }
        )

    def _handle_start_instances(self, operation: ProviderOperation) -> ProviderResult:
        handler = self._get_handler_for_operation(operation)
        result = handler.start_instances(
            instance_ids=operation.parameters.get("instance_ids", []) or [],
            context=self._build_handler_context(operation),
        )
        return ProviderResult.success_result(result, {"operation": "start_instances"})

    def _handle_stop_instances(self, operation: ProviderOperation) -> ProviderResult:
        handler = self._get_handler_for_operation(operation)
        result = handler.stop_instances(
            instance_ids=operation.parameters.get("instance_ids", []) or [],
            context=self._build_handler_context(operation),
        )
        return ProviderResult.success_result(result, {"operation": "stop_instances"})

    def _build_gcp_template_config(
        self,
        template_config: Mapping[str, object],
        count: int,
    ) -> dict[str, object]:
        defaults = GCPTemplateExtensionConfig()
        merged = dict(template_config)
        merged.setdefault("provider_type", "gcp")
        merged.setdefault("provider_api", defaults.provider_api)
        merged.setdefault("project_id", self._config.project_id)
        merged.setdefault("region", self._config.region)
        merged.setdefault("zones", self._config.zones)
        merged.setdefault("network", self._config.network)
        merged.setdefault("subnetwork", self._config.subnetwork)
        if "instance_type" not in merged and "machine_type" in merged:
            merged["instance_type"] = merged["machine_type"]
        merged.setdefault("instance_type", defaults.machine_type)
        if "boot_disk_size_gb" not in merged and "root_device_volume_size" in merged:
            merged["boot_disk_size_gb"] = merged["root_device_volume_size"]
        merged.setdefault("boot_disk_size_gb", defaults.boot_disk_size_gb)
        if "boot_disk_type" not in merged and "volume_type" in merged:
            merged["boot_disk_type"] = merged["volume_type"]
        merged.setdefault("boot_disk_type", defaults.boot_disk_type)
        merged.setdefault("source_image_family", defaults.source_image_family)
        merged.setdefault("source_image_project", defaults.source_image_project)
        merged.setdefault("provisioning_model", defaults.provisioning_model)
        merged.setdefault("network_tags", defaults.network_tags)
        merged.setdefault("labels", defaults.labels)
        merged.setdefault("instance_template_name_prefix", defaults.instance_template_name_prefix)
        merged.setdefault("max_instances", count)
        return merged

    def _get_handler_for_operation(self, operation: ProviderOperation):
        provider_api = operation.parameters.get("provider_api") or operation.parameters.get(
            "request_metadata", {}
        ).get("provider_api")
        if provider_api is None:
            provider_api = GCPProviderApi.SINGLE_VM.value
        return self._get_handler(provider_api)

    def _get_handler(self, provider_api: str | GCPProviderApi):
        return self._get_handler_factory().create_handler(provider_api)

    def _build_handler_context(self, operation: ProviderOperation) -> GCPHandlerContext:
        metadata = operation.parameters.get("request_metadata", {}) or {}
        context: GCPHandlerContext = {}
        if isinstance(metadata, dict):
            for key in (
                "project_id",
                "region",
                "zone",
                "scope",
                "mig_name",
                "instance_template_name",
                "provider_api",
            ):
                value = metadata.get(key)
                if isinstance(value, str):
                    context[key] = value
        context.setdefault("project_id", self._config.project_id)
        region = operation.parameters.get("region", self._config.region)
        if isinstance(region, str):
            context.setdefault("region", region)
        zone = operation.parameters.get("zone")
        if zone is None:
            zones = operation.parameters.get("zones") or self._config.zones
            zone = zones[0] if zones else None
        if isinstance(zone, str):
            context.setdefault("zone", zone)
        resource_ids = operation.parameters.get("resource_ids", []) or []
        if len(resource_ids) == 1:
            context.setdefault("mig_name", resource_ids[0])
        provider_api = operation.parameters.get("provider_api")
        if isinstance(provider_api, str):
            context.setdefault("provider_api", provider_api)
        return context

    def _get_compute_client(self) -> GCPComputeClient:
        if self._compute_client is None:
            raise RuntimeError("GCP compute client not initialized")
        return self._compute_client

    def _get_handler_factory(self) -> GCPHandlerFactory:
        if self._handler_factory is None:
            raise RuntimeError("GCP handler factory not initialized")
        return self._handler_factory

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type="gcp",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
                ProviderOperationType.VALIDATE_TEMPLATE,
                ProviderOperationType.GET_AVAILABLE_TEMPLATES,
                ProviderOperationType.HEALTH_CHECK,
                ProviderOperationType.RESOLVE_IMAGE,
                ProviderOperationType.START_INSTANCES,
                ProviderOperationType.STOP_INSTANCES,
            ],
            supported_apis=get_supported_apis(),
            features={
                "api_capabilities": get_supported_api_capabilities(),
                "instance_management": True,
                "spot_instances": True,
                "fleet_management": True,
                "load_balancing": True,
                "tags_support": True,
                "regions": ["us-central1", "us-east1", "europe-west4"],
                "supports_windows": True,
                "supports_linux": True,
                "auth_mode": "adc_only",
            },
            limitations={
                "requires_project_id": True,
                "requires_adc": True,
            },
            performance_metrics={
                "typical_create_time_seconds": 90,
                "typical_terminate_time_seconds": 45,
                "health_check_timeout_seconds": 10,
            },
        )

    def check_health(self) -> ProviderHealthStatus:
        return self._health_service.check_health()

    def generate_provider_name(self, config: Mapping[str, object]) -> str:
        project_id = config.get("project_id", "default")
        region = config.get("region", "global")
        return f"gcp_{project_id}_{region}"

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        parts = provider_name.split("_")
        return {
            "type": parts[0] if len(parts) > 0 else "gcp",
            "project_id": parts[1] if len(parts) > 1 else "default",
            "region": "_".join(parts[2:]) if len(parts) > 2 else "global",
        }

    def get_provider_name_pattern(self) -> str:
        return "{type}_{project_id}_{region}"

    def get_available_credential_sources(self) -> list[dict]:
        return self._health_service.get_available_credential_sources()

    def test_credentials(self, credential_source: Optional[str] = None, **kwargs) -> dict:
        return self._health_service.test_credentials(credential_source, **kwargs)

    def get_credential_requirements(self) -> dict:
        return self._health_service.get_credential_requirements()

    def get_operational_requirements(self) -> dict:
        return self._health_service.get_operational_requirements()

    def get_available_regions(self) -> list[tuple[str, str]]:
        return [
            ("us-central1", "Iowa"),
            ("us-east1", "South Carolina"),
            ("us-west1", "Oregon"),
            ("europe-west4", "Netherlands"),
        ]

    def get_default_region(self) -> str:
        return "us-central1"

    def get_cli_extra_config_keys(self) -> set[str]:
        return {"network", "subnetwork", "service_account_email", "service_account_scopes"}

    def get_cli_infrastructure_defaults(self, args: Any) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if args.gcp_network:
            result["network"] = args.gcp_network
        if args.gcp_subnetwork:
            result["subnetwork"] = args.gcp_subnetwork
        if args.gcp_service_account_email:
            result["service_account_email"] = args.gcp_service_account_email
        if args.gcp_service_account_scopes:
            result["service_account_scopes"] = [
                scope.strip()
                for scope in args.gcp_service_account_scopes.split(",")
                if scope.strip()
            ]
        return result

    def discover_infrastructure(self, provider_config: Mapping[str, object]) -> dict[str, object]:
        config = provider_config.get("config", provider_config)
        config_mapping = config if isinstance(config, Mapping) else {}
        return {
            "provider": provider_config.get("name", self._provider_name or "gcp"),
            "project_id": config_mapping.get("project_id", self._config.project_id),
            "region": config_mapping.get("region", self._config.region),
            "networks": [config_mapping.get("network")] if config_mapping.get("network") else [],
            "subnetworks": [config_mapping.get("subnetwork")] if config_mapping.get("subnetwork") else [],
        }

    def discover_infrastructure_interactive(
        self,
        provider_config: Mapping[str, object],
    ) -> dict[str, object]:
        return self.discover_infrastructure(provider_config)

    def validate_infrastructure(self, provider_config: Mapping[str, object]) -> dict[str, object]:
        config = provider_config.get("config", provider_config)
        config_mapping = config if isinstance(config, Mapping) else {}
        errors: list[str] = []
        if not config_mapping.get("project_id"):
            errors.append("project_id is required")
        if not config_mapping.get("region"):
            errors.append("region is required")
        return {"valid": len(errors) == 0, "errors": errors, "warnings": []}

    def cleanup(self) -> None:
        """Release provider-owned state."""
        self._handler_factory = None
        self._compute_client = None
        self._initialized = False
