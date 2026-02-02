from __future__ import annotations

"""AWS Provider Strategy - Orchestrator for AWS provider operations.

This module implements the ProviderStrategy interface for AWS cloud provider,
orchestrating operations through focused services while maintaining clean
architecture and single responsibility principle.
"""

import time
from typing import TYPE_CHECKING, Any, Callable, Optional

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort

# Import AWS-specific components
from providers.aws.configuration.config import AWSProviderConfig
from providers.aws.infrastructure.aws_client import AWSClient

# Import focused services
from providers.aws.services.instance_operation_service import AWSInstanceOperationService
from providers.aws.services.health_check_service import AWSHealthCheckService
from providers.aws.services.template_validation_service import AWSTemplateValidationService
from providers.aws.services.infrastructure_discovery_service import AWSInfrastructureDiscoveryService
from providers.aws.services.handler_registry import AWSHandlerRegistry
from providers.aws.services.capability_service import AWSCapabilityService

if TYPE_CHECKING:
    from providers.aws.infrastructure.adapters.aws_provisioning_adapter import AWSProvisioningAdapter
    from providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory

# Import strategy pattern interfaces
from providers.base.strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)


@injectable
class AWSProviderStrategy(ProviderStrategy):
    """
    AWS implementation of the ProviderStrategy interface.

    This class orchestrates AWS provider operations through focused services,
    maintaining clean architecture and single responsibility principle.
    Each service handles a specific aspect of AWS provider functionality.
    """

    def __init__(
        self,
        config: AWSProviderConfig,
        logger: LoggingPort,
        aws_provisioning_port: Optional[Any] = None,
        aws_provisioning_port_resolver: Optional[Callable[[], Any]] = None,
        aws_client_resolver: Optional[Callable[[], AWSClient]] = None,
        provider_name: Optional[str] = None,
        provider_instance_config: Optional[Any] = None,
    ) -> None:
        """Initialize AWS provider strategy with focused services."""
        if not isinstance(config, AWSProviderConfig):
            raise ValueError("AWSProviderStrategy requires AWSProviderConfig")

        super().__init__(config)
        self._logger = logger
        self._aws_config = config
        self._provider_instance_config = provider_instance_config
        self._aws_client: Optional[AWSClient] = None
        self._aws_client_resolver = aws_client_resolver
        self._aws_provisioning_port = aws_provisioning_port
        self._aws_provisioning_port_resolver = aws_provisioning_port_resolver
        self._provider_name = provider_name

        # Initialize services (lazy)
        self._instance_service: Optional[AWSInstanceOperationService] = None
        self._health_service: Optional[AWSHealthCheckService] = None
        self._template_service: Optional[AWSTemplateValidationService] = None
        self._infrastructure_service: Optional[AWSInfrastructureDiscoveryService] = None
        self._handler_registry: Optional[AWSHandlerRegistry] = None
        self._capability_service: Optional[AWSCapabilityService] = None

    @property
    def provider_type(self) -> str:
        """Get the provider type identifier."""
        return "aws"

    @property
    def provider_name(self) -> Optional[str]:
        """Get the provider name for this strategy."""
        return self._provider_name

    @property
    def aws_client(self) -> Optional[AWSClient]:
        """Get the AWS client instance with lazy initialization."""
        if self._aws_client is None:
            if self._aws_client_resolver:
                try:
                    self._aws_client = self._aws_client_resolver()
                    self._logger.debug("AWS client created via resolver")
                except Exception as exc:
                    self._logger.warning("Failed to resolve AWSClient: %s", exc)
            else:
                try:
                    # Need config_port to create AWS client
                    from infrastructure.di.container import get_container
                    container = get_container()
                    from domain.base.ports.configuration_port import ConfigurationPort
                    config_port = container.get(ConfigurationPort)
                    
                    self._aws_client = AWSClient(
                        config=config_port,
                        logger=self._logger,
                        provider_name=self._provider_name
                    )
                    self._logger.debug("AWS client created directly")
                except Exception as exc:
                    self._logger.warning("Failed to create AWSClient: %s", exc)
        return self._aws_client

    def initialize(self) -> bool:
        """Initialize the AWS provider strategy without creating AWS client."""
        try:
            self._logger.info("AWS provider strategy ready for region: %s", self._aws_config.region)
            self._initialized = True
            return True
        except Exception as e:
            self._logger.error("Failed to initialize AWS provider strategy: %s", e)
            return False

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """Execute a provider operation using focused AWS services."""
        self._logger.debug("AWS strategy executing operation: %s", operation.operation_type)
        
        if not self._initialized:
            return ProviderResult.error_result("AWS provider strategy not initialized", "NOT_INITIALIZED")

        start_time = time.time()
        is_dry_run = bool(operation.context and operation.context.get("dry_run", False))

        try:
            if is_dry_run:
                from providers.aws.infrastructure.dry_run_adapter import aws_dry_run_context
                with aws_dry_run_context():
                    result = await self._execute_operation_internal(operation)
            else:
                result = await self._execute_operation_internal(operation)

            execution_time_ms = int((time.time() - start_time) * 1000)
            if result.metadata is None:
                result.metadata = {}
            result.metadata.update({
                "execution_time_ms": execution_time_ms,
                "provider": "aws",
                "dry_run": is_dry_run,
            })
            return result

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            self._logger.error("AWS operation failed: %s", e)
            return ProviderResult.error_result(
                f"AWS operation failed: {e}",
                "OPERATION_FAILED",
                {"execution_time_ms": execution_time_ms, "provider": "aws", "dry_run": is_dry_run},
            )

    async def _execute_operation_internal(self, operation: ProviderOperation) -> ProviderResult:
        """Route operations to appropriate services."""
        if operation.operation_type == ProviderOperationType.CREATE_INSTANCES:
            handlers = self._get_handler_registry().get_available_handlers()
            return await self._get_instance_service().create_instances(operation, handlers)
        elif operation.operation_type == ProviderOperationType.TERMINATE_INSTANCES:
            return self._get_instance_service().terminate_instances(operation)
        elif operation.operation_type == ProviderOperationType.GET_INSTANCE_STATUS:
            return self._get_instance_service().get_instance_status(operation)
        elif operation.operation_type == ProviderOperationType.VALIDATE_TEMPLATE:
            return self._get_template_service().validate_template(operation)
        elif operation.operation_type == ProviderOperationType.HEALTH_CHECK:
            health_status = self._get_health_service().check_health()
            return ProviderResult.success_result(
                {
                    "is_healthy": health_status.is_healthy,
                    "status_message": health_status.status_message,
                    "response_time_ms": health_status.response_time_ms,
                },
                {"operation": "health_check"},
            )
        elif operation.operation_type == ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES:
            return await self._handle_describe_resource_instances(operation)
        else:
            return ProviderResult.error_result(
                f"Unsupported operation: {operation.operation_type}", "UNSUPPORTED_OPERATION"
            )

    def get_capabilities(self) -> ProviderCapabilities:
        """Get AWS provider capabilities."""
        return self._get_capability_service().get_capabilities()

    def check_health(self) -> ProviderHealthStatus:
        """Check AWS provider health status."""
        return self._get_health_service().check_health()

    def generate_provider_name(self, config: dict[str, Any]) -> str:
        """Generate AWS provider name: aws_{profile}_{region}"""
        return self._get_capability_service().generate_provider_name(config)

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        """Parse AWS provider name back to components."""
        return self._get_capability_service().parse_provider_name(provider_name)

    def get_provider_name_pattern(self) -> str:
        """Get the naming pattern for AWS providers."""
        return self._get_capability_service().get_provider_name_pattern()

    def get_supported_apis(self) -> list[str]:
        """Get supported APIs from handler registry."""
        return self._get_capability_service().get_supported_apis()



    # Service getters with lazy initialization
    def _get_handler_registry(self) -> AWSHandlerRegistry:
        """Get handler registry service with lazy initialization."""
        if self._handler_registry is None:
            handler_factory = self._get_handler_factory()
            if handler_factory:
                self._handler_registry = AWSHandlerRegistry(
                    handler_factory=handler_factory,
                    provider_instance_config=self._provider_instance_config,
                    logger=self._logger,
                )
        return self._handler_registry

    def _get_handler_factory(self) -> Optional["AWSHandlerFactory"]:
        """Get handler factory with provider-specific AWS client."""
        if self.aws_client:
            from providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory
            return AWSHandlerFactory(aws_client=self.aws_client, logger=self._logger, config=None)
        return None

    def _get_instance_service(self) -> AWSInstanceOperationService:
        """Get instance operation service with lazy initialization."""
        if self._instance_service is None:
            self._instance_service = AWSInstanceOperationService(
                aws_client=self.aws_client,
                logger=self._logger,
                provisioning_adapter=self._resolve_provisioning_port(),
                provider_name=self._provider_name,
                provider_type=self.provider_type,
            )
        return self._instance_service

    def _get_health_service(self) -> AWSHealthCheckService:
        """Get health check service with lazy initialization."""
        if self._health_service is None:
            self._health_service = AWSHealthCheckService(
                aws_client=self.aws_client, config=self._aws_config, logger=self._logger
            )
        return self._health_service

    def _get_template_service(self) -> AWSTemplateValidationService:
        """Get template validation service with lazy initialization."""
        if self._template_service is None:
            self._template_service = AWSTemplateValidationService(logger=self._logger)
        return self._template_service

    def _get_capability_service(self) -> AWSCapabilityService:
        """Get capability service with lazy initialization."""
        if self._capability_service is None:
            self._capability_service = AWSCapabilityService(
                handler_registry=self._get_handler_registry(), logger=self._logger
            )
        return self._capability_service

    def _get_infrastructure_service(self) -> AWSInfrastructureDiscoveryService:
        """Get infrastructure discovery service with lazy initialization."""
        if self._infrastructure_service is None:
            self._infrastructure_service = AWSInfrastructureDiscoveryService(
                region=self._aws_config.region, profile=self._aws_config.profile, logger=self._logger
            )
        return self._infrastructure_service

    def _resolve_provisioning_port(self) -> Optional["AWSProvisioningAdapter"]:
        """Lazily resolve the AWS provisioning adapter when first needed."""
        if self._aws_provisioning_port is None and self._aws_provisioning_port_resolver:
            try:
                self._aws_provisioning_port = self._aws_provisioning_port_resolver()
                self._logger.debug("Resolved AWS provisioning adapter via resolver")
            except Exception as exc:
                self._logger.warning("Failed to resolve AWS provisioning adapter: %s", exc)
                self._aws_provisioning_port_resolver = None
        return self._aws_provisioning_port

    # Legacy methods that need to be kept for compatibility
    async def _handle_describe_resource_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Handle resource-to-instance discovery operation using handlers."""
        return await self._get_instance_service().describe_resource_instances(operation)

    # Infrastructure discovery methods (delegated to service)
    def discover_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Discover AWS infrastructure for provider."""
        return self._get_infrastructure_service().discover_infrastructure(provider_config)

    def discover_infrastructure_interactive(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Discover AWS infrastructure interactively."""
        return self._get_infrastructure_service().discover_infrastructure_interactive(provider_config)

    def validate_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Validate AWS infrastructure configuration."""
        return self._get_infrastructure_service().validate_infrastructure(provider_config)

    # Credential methods (delegated to health service)
    def get_available_credential_sources(self) -> list[dict]:
        """Get available AWS credential sources."""
        return self._get_health_service().get_available_credential_sources()

    def test_credentials(self, credential_source: Optional[str] = None, **kwargs) -> dict:
        """Test AWS credentials."""
        return self._get_health_service().test_credentials(credential_source, **kwargs)

    def get_credential_requirements(self) -> dict:
        """AWS requires region."""
        return self._get_health_service().get_credential_requirements()

    def cleanup(self) -> None:
        """Clean up AWS provider resources."""
        try:
            if self.aws_client:
                self.aws_client.cleanup()
            self._aws_client = None
            self._initialized = False
        except Exception as e:
            self._logger.warning("Failed during AWS provider cleanup: %s", e)

    def __str__(self) -> str:
        """Return string representation for debugging."""
        return f"AWSProviderStrategy(region={self._aws_config.region}, initialized={self._initialized})"