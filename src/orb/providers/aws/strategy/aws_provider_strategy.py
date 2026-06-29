from __future__ import annotations

"""AWS Provider Strategy - Orchestrator for AWS provider operations.

This module implements the ProviderStrategy interface for AWS cloud provider,
orchestrating operations through focused services while maintaining clean
architecture and single responsibility principle.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any, Callable, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.operation_outcome import Accepted, Completed, Failed, OperationOutcome
from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort

# Import AWS-specific components
from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.services.capability_service import AWSCapabilityService
from orb.providers.aws.services.handler_registry import AWSHandlerRegistry
from orb.providers.aws.services.health_check_service import AWSHealthCheckService
from orb.providers.aws.services.infrastructure_discovery_service import (
    AWSInfrastructureDiscoveryService,
)

# Import focused services
from orb.providers.aws.services.instance_operation_service import AWSInstanceOperationService
from orb.providers.aws.services.template_validation_service import AWSTemplateValidationService

if TYPE_CHECKING:
    from orb.domain.request.aggregate import Request
    from orb.monitoring.health import HealthCheck
    from orb.providers.aws.infrastructure.adapters.aws_provisioning_adapter import (
        AWSProvisioningAdapter,
    )
    from orb.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory

# Import strategy pattern interfaces
from orb.providers.base.strategy import (
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
        config_port: Optional[ConfigurationPort] = None,
        console: Optional[Any] = None,
    ) -> None:
        """Initialize AWS provider strategy with focused services."""
        if not isinstance(config, AWSProviderConfig):
            raise ValueError("AWSProviderStrategy requires AWSProviderConfig")

        super().__init__(config)
        self._logger = logger
        self._aws_config = config
        self._console = console
        self._provider_instance_config = provider_instance_config
        self._aws_client: Optional[AWSClient] = None
        self._aws_client_resolver = aws_client_resolver
        self._aws_provisioning_port = aws_provisioning_port
        self._aws_provisioning_port_resolver = aws_provisioning_port_resolver
        self._provider_name = provider_name
        self._config_port = config_port

        # Initialize services (lazy)
        self._instance_service: Optional[AWSInstanceOperationService] = None
        self._health_service: Optional[AWSHealthCheckService] = None
        self._template_service: Optional[AWSTemplateValidationService] = None
        self._infrastructure_service: Optional[AWSInfrastructureDiscoveryService] = None
        self._handler_registry: Optional[AWSHandlerRegistry] = None
        self._capability_service: Optional[AWSCapabilityService] = None

    _API_ALIASES: dict[str, str] = {
        "AutoScalingGroup": "ASG",
        "autoscalinggroup": "ASG",
        "asg": "ASG",
    }

    @property
    def provider_type(self) -> str:
        """Get the provider type identifier."""
        return "aws"

    @classmethod
    def get_defaults_config(cls) -> dict:
        import json
        from importlib.resources import files

        from orb.providers.aws.configuration.config import AWSProviderConfig

        text = (
            files("orb.providers.aws.config")
            .joinpath("aws_defaults.json")
            .read_text(encoding="utf-8")
        )
        raw = json.loads(text)
        provider_config = raw["provider"]["providers"][0]["config"]
        AWSProviderConfig(**provider_config)  # raises ValidationError if invalid
        return raw

    def resolve_api_alias(self, raw_api: str) -> str:
        """Resolve AWS-specific API name aliases to canonical registry keys."""
        return self._API_ALIASES.get(raw_api, raw_api)

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
                    self._logger.warning("Failed to resolve AWSClient: %s", exc, exc_info=True)
            elif self._config_port is not None:
                try:
                    self._aws_client = AWSClient(
                        config=self._config_port,
                        logger=self._logger,
                        provider_name=self._provider_name,
                    )
                    self._logger.debug("AWS client created directly")
                except Exception as exc:
                    self._logger.warning("Failed to create AWSClient: %s", exc, exc_info=True)
        return self._aws_client

    def initialize(self) -> bool:
        """Initialize the AWS provider strategy without creating AWS client."""
        try:
            self._logger.info("AWS provider strategy ready for region: %s", self._aws_config.region)
            self._initialized = True
            return True
        except Exception as e:
            self._logger.error("Failed to initialize AWS provider strategy: %s", e, exc_info=True)
            return False

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """Execute a provider operation using focused AWS services."""
        self._logger.debug("AWS strategy executing operation: %s", operation.operation_type)

        if not self._initialized:
            return ProviderResult.error_result(
                "AWS provider strategy not initialized", "NOT_INITIALIZED"
            )

        start_time = time.time()
        is_dry_run = bool(operation.context and operation.context.get("dry_run", False))

        try:
            if is_dry_run:
                from orb.providers.aws.infrastructure.dry_run_adapter import aws_dry_run_context

                with aws_dry_run_context():
                    result = await self._execute_operation_internal(operation)
            else:
                result = await self._execute_operation_internal(operation)

            execution_time_ms = int((time.time() - start_time) * 1000)
            return result.model_copy(
                update={
                    "routing_info": {
                        "execution_time_ms": execution_time_ms,
                        "provider": "aws",
                    },
                    "metadata": {
                        **result.metadata,
                        "dry_run": is_dry_run,
                        "execution_time_ms": execution_time_ms,
                        "provider": "aws",
                    },
                }
            )

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            self._logger.error("AWS operation failed: %s", e, exc_info=True)
            return ProviderResult.error_result(
                f"AWS operation failed: {e}",
                "OPERATION_FAILED",
                {"dry_run": is_dry_run},
            ).model_copy(
                update={
                    "routing_info": {
                        "execution_time_ms": execution_time_ms,
                        "provider": "aws",
                    }
                }
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
        elif operation.operation_type == ProviderOperationType.RESOLVE_IMAGE:
            return await self._handle_resolve_image(operation)
        elif operation.operation_type == ProviderOperationType.START_INSTANCES:
            return self._get_instance_service().start_instances(operation)
        elif operation.operation_type == ProviderOperationType.STOP_INSTANCES:
            return self._get_instance_service().stop_instances(operation)
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
                # Get provider defaults for handler registry
                provider_defaults = self._get_provider_defaults()

                self._handler_registry = AWSHandlerRegistry(
                    handler_factory=handler_factory,
                    provider_instance_config=self._provider_instance_config,
                    provider_defaults=provider_defaults,
                    logger=self._logger,
                )
        return self._handler_registry  # type: ignore[return-value]

    def _get_provider_defaults(self) -> Optional[Any]:
        """Get provider defaults from configuration."""
        if not self._provider_instance_config:
            return None

        try:
            if self._config_port is None:
                return None
            provider_config_root = self._config_port.get_provider_config()
            if provider_config_root is None:
                return None
            return provider_config_root.provider_defaults.get(self._provider_instance_config.type)
        except Exception as e:
            if self._logger:
                self._logger.warning("Failed to get provider defaults: %s", e, exc_info=True)
            return None

    def _get_handler_factory(self) -> Optional[AWSHandlerFactory]:
        """Get handler factory with provider-specific AWS client."""
        if self.aws_client:
            from orb.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory

            return AWSHandlerFactory(
                aws_client=self.aws_client, logger=self._logger, config=self._config_port
            )
        return None

    def get_handler(self, handler_type: str) -> Optional[Any]:
        """Get AWS handler by type — delegates to handler registry."""
        registry = self._get_handler_registry()
        if registry:
            return registry.get_handler(handler_type)
        return None

    def _get_instance_service(self) -> AWSInstanceOperationService:
        """Get instance operation service with lazy initialization."""
        if self._instance_service is None:
            from orb.providers.aws.infrastructure.adapters.aws_provisioning_adapter import (
                AWSProvisioningAdapter,
            )
            from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter

            provisioning_adapter = AWSProvisioningAdapter(
                aws_client=self.aws_client,  # type: ignore[arg-type]
                logger=self._logger,
                provider_strategy=self,
                config_port=self._config_port,
            )
            machine_adapter = AWSMachineAdapter(
                aws_client=self.aws_client,  # type: ignore[arg-type]
                logger=self._logger,
            )
            self._instance_service = AWSInstanceOperationService(
                aws_client=self.aws_client,  # type: ignore[arg-type]
                logger=self._logger,
                provisioning_adapter=provisioning_adapter,
                machine_adapter=machine_adapter,
                provider_name=self._provider_name,
                provider_type=self.provider_type,
            )
        return self._instance_service

    def _get_health_service(self) -> AWSHealthCheckService:
        """Get health check service with lazy initialization."""
        if self._health_service is None:
            self._health_service = AWSHealthCheckService(
                aws_client=self.aws_client,  # type: ignore[arg-type]
                config=self._aws_config,
                logger=self._logger,
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
                region=self._aws_config.region,
                profile=self._aws_config.profile or None,
                logger=self._logger,
                console=self._console,
            )
        return self._infrastructure_service

    def _resolve_provisioning_port(self) -> Optional[AWSProvisioningAdapter]:
        """Lazily resolve the AWS provisioning adapter when first needed."""
        if self._aws_provisioning_port is None and self._aws_provisioning_port_resolver:
            try:
                self._aws_provisioning_port = self._aws_provisioning_port_resolver()
                self._logger.debug("Resolved AWS provisioning adapter via resolver")
            except Exception as exc:
                self._logger.warning(
                    "Failed to resolve AWS provisioning adapter: %s", exc, exc_info=True
                )
                self._aws_provisioning_port_resolver = None
        return self._aws_provisioning_port

    async def _handle_describe_resource_instances(
        self, operation: ProviderOperation
    ) -> ProviderResult:
        """Handle resource-to-instance discovery using the appropriate AWS handler.

        Each handler (EC2Fleet, SpotFleet, ASG, RunInstances) has fleet-type-aware
        logic for discovering instances from resource IDs. This delegates to the
        correct handler's check_hosts_status method rather than using a generic
        service that lacks per-handler context.

        The handler returns a CheckHostsStatusResult containing both instance
        details and a ProviderFulfilment verdict.  The fulfilment is forwarded
        in metadata so RequestStatusService can consume it without any
        provider-specific logic.
        """
        try:
            from orb.domain.base.provider_fulfilment import ProviderFulfilment

            resource_ids = operation.parameters.get("resource_ids", [])
            provider_api = operation.parameters.get("provider_api", "RunInstances")
            provider_api_value = (
                provider_api.value if hasattr(provider_api, "value") else provider_api
            )

            if not resource_ids:
                return ProviderResult.error_result(
                    "Resource IDs are required for instance discovery",
                    "MISSING_RESOURCE_IDS",
                )

            handler = self.get_handler(provider_api_value)
            if not handler:
                handler = self.get_handler("RunInstances")
                if not handler:
                    return ProviderResult.error_result(
                        f"No handler available for provider_api: {provider_api}",
                        "HANDLER_NOT_FOUND",
                    )
                self._logger.warning(
                    "Handler for %s not found, using RunInstances fallback", provider_api
                )

            from orb.domain.request.aggregate import Request
            from orb.domain.request.value_objects import RequestType

            request_id = operation.parameters.get("request_id") or (
                operation.context.get("request_id") if operation.context else None
            )

            requested_count = int(operation.parameters.get("requested_count") or 1)
            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id=operation.parameters.get("template_id", "unknown"),
                machine_count=requested_count,
                provider_type="aws",
                provider_name="aws-default",
                request_id=request_id,
            )
            request.resource_ids = resource_ids

            # check_hosts_status makes blocking boto3 I/O calls (describe_fleet_instances,
            # describe_instances, etc.).  Running it directly in an async handler blocks
            # the uvicorn event loop, which prevents uvicorn from accepting any further
            # connections until the call completes.  For large requests (e.g. 100 instances)
            # this starvation causes all concurrent polls to fail with ConnectionError.
            # Offloading to a thread pool executor keeps the event loop responsive.
            check_result = await asyncio.to_thread(handler.check_hosts_status, request)
            instance_details = check_result.instances
            fulfilment: ProviderFulfilment = check_result.fulfilment

            metadata: dict[str, Any] = {
                "operation": "describe_resource_instances",
                "resource_ids": resource_ids,
                "provider_api": provider_api_value,
                "instance_count": len(instance_details),
                # Forward the provider's fulfilment verdict to the application layer.
                # RequestStatusService reads this as "provider_fulfilment" and trusts
                # it exclusively — no count math or AWS-specific key inspection.
                "provider_fulfilment": fulfilment,
            }

            if not instance_details:
                self._logger.info("No instances found for resources: %s", resource_ids)
                return ProviderResult.success_result({"instances": []}, metadata)

            return ProviderResult.success_result(
                data={"instances": instance_details},
                metadata=metadata,
            )

        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to describe resource instances: {e!s}",
                "DESCRIBE_RESOURCE_INSTANCES_ERROR",
            )

    # -------------------------------------------------------------------------
    # Infrastructure discovery methods (delegated to service)
    # -------------------------------------------------------------------------

    def discover_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Discover AWS infrastructure for provider."""
        return self._get_infrastructure_service().discover_infrastructure(provider_config)

    def discover_infrastructure_interactive(
        self, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Discover AWS infrastructure interactively."""
        # Create fresh infrastructure service with runtime config
        config = provider_config.get("config", {})
        region = config.get("region", self._aws_config.region)
        profile = config.get("profile", self._aws_config.profile)

        infrastructure_service = AWSInfrastructureDiscoveryService(
            region=region, profile=profile, logger=self._logger, console=self._console
        )
        return infrastructure_service.discover_infrastructure_interactive(provider_config)

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

    def get_operational_requirements(self) -> dict:
        """Get operational requirements for AWS."""
        return self._get_health_service().get_operational_requirements()

    def get_available_regions(self) -> list[tuple[str, str]]:
        """Get common AWS regions as (region_id, display_name) tuples."""
        return [
            ("us-east-1", "N. Virginia"),
            ("us-east-2", "Ohio"),
            ("us-west-1", "N. California"),
            ("us-west-2", "Oregon"),
            ("eu-west-1", "Ireland"),
            ("eu-west-2", "London"),
            ("eu-central-1", "Frankfurt"),
            ("ap-southeast-1", "Singapore"),
            ("ap-southeast-2", "Sydney"),
            ("ap-northeast-1", "Tokyo"),
            ("ca-central-1", "Canada"),
            ("sa-east-1", "São Paulo"),
        ]

    def get_default_region(self) -> str:
        """Return the default AWS region for CLI prompts."""
        return "us-east-1"

    def get_cli_extra_config_keys(self) -> set[str]:
        """Return AWS keys that belong in provider config, not template_defaults."""
        return {"fleet_role"}

    def get_cli_infrastructure_defaults(self, args: Any) -> dict[str, Any]:
        """Extract AWS-specific infrastructure defaults from parsed CLI args."""
        result: dict[str, Any] = {}
        if getattr(args, "subnet_ids", None):
            result["subnet_ids"] = [s.strip() for s in args.subnet_ids.split(",")]
        if getattr(args, "security_group_ids", None):
            result["security_group_ids"] = [s.strip() for s in args.security_group_ids.split(",")]
        if getattr(args, "fleet_role", None):
            result["fleet_role"] = args.fleet_role
        return result

    def register_health_checks(self, health_check: HealthCheck) -> None:
        """Register AWS-specific health checks if client is available."""
        if self.aws_client is None:
            return
        from orb.providers.aws.health import register_aws_health_checks

        storage_strategy = "json"
        if self._config_port is not None:
            try:
                storage_strategy = self._config_port.get_storage_strategy()
            except Exception:
                pass
        register_aws_health_checks(health_check, self.aws_client, storage_strategy)

    def cleanup(self) -> None:
        """Clean up AWS provider resources."""
        try:
            if self.aws_client:
                self.aws_client.cleanup()  # type: ignore[attr-defined]
            self._aws_client = None
            self._initialized = False
        except Exception as e:
            self._logger.warning("Failed during AWS provider cleanup: %s", e, exc_info=True)

    async def _handle_resolve_image(self, operation: ProviderOperation) -> ProviderResult:
        """Handle image resolution using registry-based service."""
        try:
            image_specifications = operation.parameters.get("image_specifications", [])
            if not image_specifications:
                return ProviderResult.success_result({"resolved_images": {}})

            # Partition specs — only create the service (and activate aws_client) if needed
            needs_resolution = [s for s in image_specifications if not s.startswith("ami-")]

            resolved_images = {s: s for s in image_specifications}  # default: pass-through

            if needs_resolution:
                service = self._create_image_resolution_service()
                for spec in needs_resolution:
                    resolved_images[spec] = service.resolve_image_id(spec)

            return ProviderResult.success_result({"resolved_images": resolved_images})

        except Exception as e:
            return ProviderResult.error_result(f"Image resolution failed: {e!s}")

    def _create_image_resolution_service(self):
        """Create AWS image resolution service with provider-specific context."""
        from orb.providers.aws.infrastructure.caching.aws_image_cache import AWSImageCache
        from orb.providers.aws.infrastructure.services.aws_image_resolution_service import (
            AWSImageResolutionService,
        )

        cache_dir = self._config_port.get_cache_dir() if self._config_port else ""

        cache = AWSImageCache(
            provider_name=getattr(self, "provider_name", "aws"),
            cache_dir=cache_dir,
            ttl_seconds=3600,
        )

        aws_client = self.aws_client
        if aws_client is None:
            raise RuntimeError("AWS client not available for image resolution")

        return AWSImageResolutionService(
            aws_client=aws_client,
            cache=cache,
            logger=self._logger,
        )

    # ------------------------------------------------------------------
    # Typed provisioning interface — OperationOutcome
    # ------------------------------------------------------------------

    async def acquire(self, request: Request) -> OperationOutcome:
        """Submit an acquisition request to AWS.

        AWS provider operations are asynchronous: the API call (EC2Fleet,
        SpotFleet, RunInstances, ASG) returns a request/fleet ID immediately
        while instances transition through ``pending``.  The outcome is
        therefore always ``Accepted`` on success.

        Args:
            request: Domain request describing resources to acquire.

        Returns:
            ``Accepted`` with pending instance IDs on success.
            ``Failed`` on provider rejection or configuration error.
        """
        try:
            # Build operation using the strategy-layer types already imported at the
            # module level (ProviderOperation / ProviderOperationType from
            # orb.providers.base.strategy).  These are what execute_operation() expects.
            operation = ProviderOperation(
                operation_type=ProviderOperationType.CREATE_INSTANCES,
                parameters={
                    "template_config": {},
                    "count": request.requested_count,
                    "request_id": str(request.request_id),
                    "request_metadata": dict(request.metadata),
                },
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
                    "dry_run": request.metadata.get("dry_run", False),
                },
            )
            result = await self.execute_operation(operation)

            if not result.success:
                return Failed(
                    error=result.error_message or "AWS acquire failed",
                    recoverable=False,
                )

            resource_ids: list[str] = (result.data or {}).get("resource_ids", [])
            request_id = str(request.request_id)

            self._logger.info(
                "AWS acquire accepted: request_id=%s, pending_resource_ids=%s",
                request_id,
                resource_ids,
            )
            return Accepted(
                request_id=request_id,
                pending_resource_ids=resource_ids,
                metadata=result.metadata or {},
            )

        except Exception as exc:
            self._logger.error("AWS acquire failed: %s", exc, exc_info=True)
            return Failed(error=str(exc), recoverable=False)

    async def return_machines(self, machine_ids: list[str], request: Request) -> OperationOutcome:
        """Submit a return (termination) request to AWS.

        AWS terminates asynchronously — ``TerminateInstances`` returns
        immediately while instances move through ``shutting-down``.  The
        outcome is therefore ``Accepted`` with the terminating IDs.

        Args:
            machine_ids: EC2 instance IDs to terminate.
            request: Domain request providing context.

        Returns:
            ``Accepted`` with terminating instance IDs on success.
            ``Failed`` on provider rejection or configuration error.
        """
        try:
            operation = ProviderOperation(
                operation_type=ProviderOperationType.TERMINATE_INSTANCES,
                parameters={
                    "instance_ids": machine_ids,
                    "request_id": str(request.request_id),
                    "template_id": request.template_id,
                    "provider_api": request.provider_api or "RunInstances",
                },
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
                },
            )
            result = await self.execute_operation(operation)

            if not result.success:
                return Failed(
                    error=result.error_message or "AWS return_machines failed",
                    recoverable=False,
                )

            self._logger.info(
                "AWS termination accepted: request_id=%s, terminating=%s",
                request.request_id,
                machine_ids,
            )
            return Accepted(
                request_id=str(request.request_id),
                pending_resource_ids=list(machine_ids),
                metadata=result.metadata or {},
            )

        except Exception as exc:
            self._logger.error("AWS return_machines failed: %s", exc, exc_info=True)
            return Failed(error=str(exc), recoverable=False)

    async def get_status(self, resource_ids: list[str], request: Request) -> OperationOutcome:
        """Query AWS instance status for previously submitted resources.

        Returns ``Completed`` only when *all* instances have reached a
        terminal state (``running`` for acquire, ``terminated`` for return).
        Returns ``Accepted`` (still in-progress) otherwise.

        Args:
            resource_ids: EC2 instance or resource IDs to check.
            request: Domain request providing context.

        Returns:
            ``Completed`` when all instances are terminal.
            ``Accepted``  when one or more instances are still transitioning.
            ``Failed``    when all instances failed or a hard error occurred.
        """
        try:
            operation = ProviderOperation(
                operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
                parameters={
                    "instance_ids": resource_ids,
                    "template_id": request.template_id,
                    "provider_api": request.provider_api or "RunInstances",
                },
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
                },
            )
            result = await self.execute_operation(operation)

            if not result.success:
                return Failed(
                    error=result.error_message or "AWS get_status failed",
                    recoverable=True,
                )

            instances: list[dict[str, Any]] = (result.data or {}).get("instances", [])
            terminal_states = frozenset({"running", "terminated", "stopped", "failed"})
            non_terminal = [
                inst for inst in instances if inst.get("status", "") not in terminal_states
            ]

            if non_terminal:
                still_pending = [inst.get("instance_id", "") for inst in non_terminal]
                return Accepted(
                    request_id=str(request.request_id),
                    pending_resource_ids=still_pending,
                    metadata=result.metadata or {},
                )

            completed_ids = [inst.get("instance_id", "") for inst in instances]
            return Completed(
                resource_ids=completed_ids,
                metadata=result.metadata or {},
            )

        except Exception as exc:
            self._logger.error("AWS get_status failed: %s", exc, exc_info=True)
            return Failed(error=str(exc), recoverable=True)

    def __str__(self) -> str:
        """Return string representation for debugging."""
        return f"AWSProviderStrategy(region={self._aws_config.region}, initialized={self._initialized})"
