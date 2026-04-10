"""GCP provider strategy."""

from __future__ import annotations

import time
from typing import Any, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
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
from orb.providers.gcp.exceptions import GCPError, translate_gcp_exception
from orb.providers.gcp.infrastructure import (
    GCPComputeClient,
    GCPHandlerFactory,
)
from orb.providers.gcp.services import (
    GCPExecutionService,
    GCPHealthCheckService,
    GCPInventoryService,
    GCPMutationService,
    GCPOperationContextService,
    GCPProvisioningService,
)


@injectable
class GCPProviderStrategy(ProviderStrategy):
    """GCP implementation of the provider strategy interface."""

    def __init__(
        self,
        config: GCPProviderConfig,
        logger: LoggingPort,
        provider_name: Optional[str] = None,
    ) -> None:
        """Store provider dependencies and defer client initialization until startup."""
        if not isinstance(config, GCPProviderConfig):
            raise ValueError("GCPProviderStrategy requires GCPProviderConfig")
        super().__init__(config)
        self._config = config
        self._logger = logger
        self._provider_name = provider_name
        self._execution_service = GCPExecutionService()
        self._health_service = GCPHealthCheckService(config=config, logger=logger)
        self._inventory_service = GCPInventoryService()
        self._mutation_service = GCPMutationService()
        self._provisioning_service = GCPProvisioningService()
        self._compute_client: Optional[GCPComputeClient] = None
        self._handler_factory: Optional[GCPHandlerFactory] = None

    @property
    def provider_type(self) -> str:
        """Return the provider type handled by this strategy."""
        return "gcp"

    @classmethod
    def get_defaults_config(cls) -> dict:
        """Expose template defaults used when generating provider config."""
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
        """Initialize the GCP runtime clients and handler factory."""
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
        create_context = self._get_operation_context_service().build_create_context(operation)
        if isinstance(create_context, ProviderResult):
            return create_context
        outcome = self._execution_service.execute_create(create_context)
        return self._provisioning_service.build_provider_result(
            context=create_context,
            outcome=outcome,
        )

    def _handle_terminate_instances(self, operation: ProviderOperation) -> ProviderResult:
        mutation_context = self._get_operation_context_service().build_mutation_context(operation)
        outcome = self._execution_service.execute_terminate(mutation_context)
        return self._mutation_service.build_provider_result(
            operation_name="terminate_instances",
            outcome=outcome,
            metadata={
                "instance_ids": mutation_context.instance_ids,
                "resource_ids": mutation_context.resource_ids,
            },
        )

    def _handle_get_instance_status(self, operation: ProviderOperation) -> ProviderResult:
        mutation_context = self._get_operation_context_service().build_mutation_context(operation)
        instances = self._execution_service.execute_status(mutation_context)
        return self._inventory_service.build_status_result(
            operation_name="get_instance_status",
            instances=instances,
        )

    def _handle_describe_resource_instances(self, operation: ProviderOperation) -> ProviderResult:
        mutation_context = self._get_operation_context_service().build_mutation_context(operation)
        instances = self._execution_service.execute_status(mutation_context)
        return self._inventory_service.build_status_result(
            operation_name="describe_resource_instances",
            instances=instances,
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
        mutation_context = self._get_operation_context_service().build_mutation_context(operation)
        outcome = self._execution_service.execute_start(mutation_context)
        return self._mutation_service.build_provider_result(
            operation_name="start_instances",
            outcome=outcome,
        )

    def _handle_stop_instances(self, operation: ProviderOperation) -> ProviderResult:
        mutation_context = self._get_operation_context_service().build_mutation_context(operation)
        outcome = self._execution_service.execute_stop(mutation_context)
        return self._mutation_service.build_provider_result(
            operation_name="stop_instances",
            outcome=outcome,
        )

    def _get_compute_client(self) -> GCPComputeClient:
        if self._compute_client is None:
            raise RuntimeError("GCP compute client not initialized")
        return self._compute_client

    def _get_handler_factory(self) -> GCPHandlerFactory:
        if self._handler_factory is None:
            raise RuntimeError("GCP handler factory not initialized")
        return self._handler_factory

    def _get_operation_context_service(self) -> GCPOperationContextService:
        return GCPOperationContextService(
            config=self._config,
            handler_factory=self._get_handler_factory(),
            provider_name=self._provider_name,
        )

    def get_capabilities(self) -> ProviderCapabilities:
        """Describe the operations and features supported by the GCP provider."""
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
        """Run the provider health check service."""
        return self._health_service.check_health()

    def generate_provider_name(self, config: Mapping[str, object]) -> str:
        """Generate a provider name from GCP project and region values."""
        project_id = config.get("project_id", "default")
        region = config.get("region", "global")
        return f"gcp_{project_id}_{region}"

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        """Parse a provider name generated by this strategy."""
        parts = provider_name.split("_")
        return {
            "type": parts[0] if len(parts) > 0 else "gcp",
            "project_id": parts[1] if len(parts) > 1 else "default",
            "region": "_".join(parts[2:]) if len(parts) > 2 else "global",
        }

    def get_provider_name_pattern(self) -> str:
        """Return the expected provider-name pattern."""
        return "{type}_{project_id}_{region}"

    def get_available_credential_sources(self) -> list[dict]:
        """List credential sources visible to the health check service."""
        return self._health_service.get_available_credential_sources()

    def test_credentials(self, credential_source: Optional[str] = None, **kwargs) -> dict:
        """Validate access using the selected credential source."""
        return self._health_service.test_credentials(credential_source, **kwargs)

    def get_credential_requirements(self) -> dict:
        """Return the credential requirements for the GCP provider."""
        return self._health_service.get_credential_requirements()

    def get_operational_requirements(self) -> dict:
        """Return non-credential operational requirements for the GCP provider."""
        return self._health_service.get_operational_requirements()

    def get_available_regions(self) -> list[tuple[str, str]]:
        """List representative regions exposed through the CLI surface."""
        return [
            ("us-central1", "Iowa"),
            ("us-east1", "South Carolina"),
            ("us-west1", "Oregon"),
            ("europe-west4", "Netherlands"),
        ]

    def get_default_region(self) -> str:
        """Return the default region for new GCP providers."""
        return "us-central1"

    def get_cli_extra_config_keys(self) -> set[str]:
        """List additional template keys accepted from GCP CLI arguments."""
        return {"network", "subnetwork", "service_account_email", "service_account_scopes"}

    def get_cli_infrastructure_defaults(self, args: Any) -> dict[str, Any]:
        """Extract infrastructure defaults from parsed GCP CLI arguments."""
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
        """Return the configured infrastructure fields visible to the GCP provider."""
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
        """Return discovered infrastructure without prompting for extra input."""
        return self.discover_infrastructure(provider_config)

    def validate_infrastructure(self, provider_config: Mapping[str, object]) -> dict[str, object]:
        """Validate the minimum infrastructure fields required by the provider."""
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
