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
        if self._aws_client is None and self._aws_client_resolver:
            try:
                self._aws_client = self._aws_client_resolver()
                self._logger.debug("AWS client created via resolver")
            except Exception as exc:
                self._logger.warning("Failed to resolve AWSClient: %s", exc)
        return self._aws_client

    def initialize(self) -> bool:
        """Initialize the AWS provider strategy without creating AWS client."""
        try:
            self._logger.info("AWS provider strategy ready for region: %s", self._aws_config.region)
            self._initialized = True
            self._logger.debug("AWS provider strategy initialized successfully (lazy mode)")
            return True
        except Exception as e:
            self._logger.error("Failed to initialize AWS provider strategy: %s", e)
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
            # Execute operation within appropriate context
            if is_dry_run:
                from providers.aws.infrastructure.dry_run_adapter import aws_dry_run_context
                with aws_dry_run_context():
                    result = await self._execute_operation_internal(operation)
            else:
                result = await self._execute_operation_internal(operation)

            # Add execution metadata
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
                {
                    "execution_time_ms": execution_time_ms,
                    "provider": "aws",
                    "dry_run": is_dry_run,
                },
            )

    async def _execute_operation_internal(self, operation: ProviderOperation) -> ProviderResult:
        """Route operations to appropriate services."""
        # Route to focused services
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
        elif operation.operation_type == ProviderOperationType.GET_AVAILABLE_TEMPLATES:
            return self._handle_get_available_templates(operation)
        else:
            return ProviderResult.error_result(
                f"Unsupported operation: {operation.operation_type}",
                "UNSUPPORTED_OPERATION",
            )

    def get_capabilities(self) -> ProviderCapabilities:
        """Get AWS provider capabilities."""
        # Get effective handlers for this provider instance
        if self._provider_instance_config:
            # Try to get provider defaults from config manager
            try:
                from infrastructure.di.container import get_container
                from domain.base.ports import ConfigurationPort
                
                container = get_container()
                config_manager = container.get(ConfigurationPort)
                app_config = config_manager.app_config
                provider_defaults = app_config.provider.provider_defaults.get('aws')
                
                effective_handlers = self._provider_instance_config.get_effective_handlers(provider_defaults)
                supported_apis = list(effective_handlers.keys())
            except Exception:
                # Fallback to default handlers if config loading fails
                supported_apis = ["EC2Fleet", "SpotFleet", "ASG", "RunInstances"]
        else:
            # Default AWS handlers
            supported_apis = ["EC2Fleet", "SpotFleet", "ASG", "RunInstances"]
        
        return ProviderCapabilities(
            provider_type="aws",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
                ProviderOperationType.VALIDATE_TEMPLATE,
                ProviderOperationType.HEALTH_CHECK,
            ],
            supported_apis=supported_apis,
            features={
                "instance_management": True,
                "spot_instances": True,
                "fleet_management": True,
                "auto_scaling": True,
                "load_balancing": True,
                "vpc_support": True,
                "security_groups": True,
                "key_pairs": True,
                "tags_support": True,
                "monitoring": True,
                "regions": ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"],
                "instance_types": ["t3.micro", "t3.small", "t3.medium", "m5.large", "c5.large"],
                "max_instances_per_request": 1000,
                "supports_windows": True,
                "supports_linux": True,
            }
        )

    def check_health(self) -> ProviderHealthStatus:
        """Check AWS provider health."""
        try:
            # Simple health check - try to get AWS client
            aws_client = self._get_aws_client()
            if aws_client:
                return ProviderHealthStatus(
                    is_healthy=True,
                    status_message="AWS provider is operational",
                    last_check_time=str(time.time())
                )
            else:
                return ProviderHealthStatus(
                    is_healthy=False,
                    status_message="AWS client not available",
                    last_check_time=str(time.time())
                )
        except Exception as e:
            return ProviderHealthStatus(
                is_healthy=False,
                status_message=f"Health check failed: {e}",
                last_check_time=str(time.time())
            )

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
            return AWSHandlerFactory(
                aws_client=self.aws_client,
                logger=self._logger,
                config=None
            )
        return None

    def _get_instance_service(self) -> AWSInstanceOperationService:
        """Get instance operation service with lazy initialization."""
        if self._instance_service is None:
            provisioning_adapter = self._resolve_provisioning_port()
            self._instance_service = AWSInstanceOperationService(
                aws_client=self.aws_client,
                logger=self._logger,
                provisioning_adapter=provisioning_adapter,
                provider_name=self._provider_name,
                provider_type=self.provider_type,
            )
        return self._instance_service

    def _get_health_service(self) -> AWSHealthCheckService:
        """Get health check service with lazy initialization."""
        if self._health_service is None:
            self._health_service = AWSHealthCheckService(
                aws_client=self.aws_client,
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
            handler_registry = self._get_handler_registry()
            self._capability_service = AWSCapabilityService(
                handler_registry=handler_registry,
                logger=self._logger,
            )
        return self._capability_service

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
        try:
            resource_ids = operation.parameters.get("resource_ids", [])
            provider_api = operation.parameters.get("provider_api", "RunInstances")
            
            if not resource_ids:
                return ProviderResult.error_result(
                    "Resource IDs are required for instance discovery",
                    "MISSING_RESOURCE_IDS",
                )

            # Get handler from registry
            handler_registry = self._get_handler_registry()
            handlers = handler_registry.get_available_handlers()
            handler = handlers.get(provider_api) or handlers.get("RunInstances")
            
            if not handler:
                return ProviderResult.error_result(
                    f"No handler available for provider_api: {provider_api}",
                    "HANDLER_NOT_FOUND",
                )

            # Create minimal request for handler
            from domain.request.aggregate import Request
            from domain.request.value_objects import RequestType

            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id=operation.parameters.get("template_id", "unknown"),
                machine_count=1,
                provider_type=self.provider_type,
                provider_name=self.provider_name,
                request_id=operation.parameters.get("request_id"),
            )
            request.resource_ids = resource_ids

            # Use handler to check status
            instance_details = handler.check_hosts_status(request)
            
            if not instance_details:
                return ProviderResult.success_result(
                    {"instances": []},
                    {"operation": "describe_resource_instances", "resource_ids": resource_ids},
                )

            # Format instances for output
            formatted_instances = []
            for instance_data in instance_details:
                formatted_instance = {
                    "InstanceId": instance_data.get("instance_id") or instance_data.get("InstanceId"),
                    "State": instance_data.get("status") or instance_data.get("State", "unknown"),
                    "PrivateIpAddress": instance_data.get("private_ip") or instance_data.get("PrivateIpAddress"),
                    "PublicIpAddress": instance_data.get("public_ip") or instance_data.get("PublicIpAddress"),
                    "LaunchTime": instance_data.get("launch_time") or instance_data.get("LaunchTime"),
                    "InstanceType": instance_data.get("instance_type") or instance_data.get("InstanceType"),
                    "SubnetId": instance_data.get("subnet_id") or instance_data.get("SubnetId"),
                    "VpcId": instance_data.get("vpc_id") or instance_data.get("VpcId"),
                }
                formatted_instances.append(formatted_instance)

            return ProviderResult.success_result(
                {"instances": formatted_instances},
                {
                    "operation": "describe_resource_instances",
                    "resource_ids": resource_ids,
                    "provider_api": provider_api,
                    "instance_count": len(formatted_instances),
                },
            )

        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to describe resource instances: {e}",
                "DESCRIBE_RESOURCE_INSTANCES_ERROR",
            )

    def _handle_get_available_templates(self, operation: ProviderOperation) -> ProviderResult:
        """Handle available templates query operation."""
        try:
            templates = self._get_aws_templates()
            return ProviderResult.success_result(
                {"templates": templates, "count": len(templates)},
                {"operation": "get_available_templates"},
            )
        except Exception as e:
            return ProviderResult.error_result(f"Failed to get available templates: {e}", "GET_TEMPLATES_ERROR")

    def _get_aws_templates(self) -> list[dict[str, Any]]:
        """Get available AWS templates using scheduler strategy."""
        try:
            from infrastructure.scheduler.registry import get_scheduler_registry

            scheduler_registry = get_scheduler_registry()
            scheduler_strategy = scheduler_registry.get_active_strategy()

            if scheduler_strategy:
                template_paths = scheduler_strategy.get_template_paths()
                templates = []
                for template_path in template_paths:
                    try:
                        template_data = scheduler_strategy.load_templates_from_path(template_path)
                        templates.extend(template_data)
                    except Exception as e:
                        self._logger.warning("Failed to load templates from %s: %s", template_path, e)
                return templates
            else:
                self._logger.warning("No scheduler strategy available, using fallback templates")
                return self._get_fallback_templates()

        except Exception as e:
            self._logger.error("Failed to load templates via scheduler strategy: %s", e)
            return self._get_fallback_templates()

    def _get_fallback_templates(self) -> list[dict[str, Any]]:
        """Get fallback AWS templates when scheduler strategy is not available."""
        return [
            {
                "template_id": "aws-linux-basic",
                "name": "Amazon Linux 2 Basic",
                "image_id": "ami-0abcdef1234567890",
                "instance_type": "t3.micro",
                "description": "Basic Amazon Linux 2 instance",
            },
            {
                "template_id": "aws-ubuntu-basic",
                "name": "Ubuntu 20.04 Basic",
                "image_id": "ami-0fedcba0987654321",
                "instance_type": "t3.small",
                "description": "Basic Ubuntu 20.04 instance",
            },
        ]

    # Infrastructure discovery methods (delegated to service)
    def discover_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Discover AWS infrastructure for provider."""
        try:
            config = provider_config.get("config", {})
            cli_args = provider_config.get("cli_args")
            
            discovery = AWSInfrastructureDiscoveryService(
                region=config.get("region", "us-east-1"),
                profile=config.get("profile", "default")
            )

            # Handle summary flag
            if cli_args and getattr(cli_args, 'summary', False):
                return self._discover_infrastructure_summary(provider_config, discovery)
            
            # Handle show flag (filter resources)
            show_filter = None
            show_all = False
            if cli_args and hasattr(cli_args, 'show') and cli_args.show is not None:
                if not cli_args.show.strip():
                    from cli.console import print_error, print_info
                    print_error("--show flag requires resource types")
                    print_info("Available resources: vpcs, subnets, security-groups (or sg), all")
                    return {"provider": provider_config.get("name", "unknown"), "error": "Invalid --show argument"}
                
                show_filter = [s.strip() for s in cli_args.show.split(',')]
                show_filter = [f.replace('sg', 'security-groups') for f in show_filter]
                
                if 'all' in show_filter:
                    show_all = True
                    show_filter = None

            # Handle all flag
            if cli_args and getattr(cli_args, 'all', False):
                show_all = True

            vpcs = discovery.discover_vpcs()
            from cli.console import print_info, print_separator
            
            print_info(f"\nProvider: {provider_config.get('name', 'unknown')}")
            print_info(f"Region: {config.get('region', 'us-east-1')}")
            print_separator(width=50, char="-")

            if not vpcs:
                print_info("No VPCs found")
                return {"provider": provider_config.get("name", "unknown"), "vpcs": 0}

            print_info(f"Found {len(vpcs)} VPCs:")
            total_subnets = 0
            total_sgs = 0

            for vpc in vpcs:
                print_info(f"  {vpc}")
                
                if not show_filter or 'subnets' in show_filter:
                    subnets = discovery.discover_subnets(vpc.id)
                    total_subnets += len(subnets)
                    if subnets:
                        print_info(f"    Subnets ({len(subnets)}):")
                        display_count = len(subnets) if show_all else min(3, len(subnets))
                        for subnet in subnets[:display_count]:
                            print_info(f"      {subnet}")
                        if not show_all and len(subnets) > 3:
                            print_info(f"      ... and {len(subnets) - 3} more")

                if not show_filter or 'security-groups' in show_filter:
                    sgs = discovery.discover_security_groups(vpc.id)
                    total_sgs += len(sgs)
                    if sgs:
                        print_info(f"    Security Groups ({len(sgs)}):")
                        display_count = len(sgs) if show_all else min(2, len(sgs))
                        for sg in sgs[:display_count]:
                            print_info(f"      {sg}")
                        if not show_all and len(sgs) > 2:
                            print_info(f"      ... and {len(sgs) - 2} more")

            return {
                "provider": provider_config.get("name", "unknown"),
                "vpcs": len(vpcs),
                "total_subnets": total_subnets,
                "total_sgs": total_sgs,
            }

        except Exception as e:
            from cli.console import print_error
            print_error(f"Failed to discover infrastructure: {e}")
            return {"provider": provider_config.get("name", "unknown"), "error": str(e)}

    def _discover_infrastructure_summary(self, provider_config: dict[str, Any], discovery) -> dict[str, Any]:
        """Discover infrastructure summary (counts only)."""
        from cli.console import print_info, print_separator
        
        config = provider_config.get("config", {})
        vpcs = discovery.discover_vpcs()
        
        print_info(f"\nProvider: {provider_config.get('name', 'unknown')}")
        print_info(f"Region: {config.get('region', 'us-east-1')}")
        print_separator(width=50, char="-")
        
        if not vpcs:
            print_info("No infrastructure found")
            return {"provider": provider_config.get("name", "unknown"), "vpcs": 0}
        
        total_subnets = sum(len(discovery.discover_subnets(vpc.id)) for vpc in vpcs)
        total_sgs = sum(len(discovery.discover_security_groups(vpc.id)) for vpc in vpcs)
        
        print_info(f"Infrastructure Summary:")
        print_info(f"  VPCs: {len(vpcs)}")
        print_info(f"  Subnets: {total_subnets}")
        print_info(f"  Security Groups: {total_sgs}")
        
        return {
            "provider": provider_config.get("name", "unknown"),
            "vpcs": len(vpcs),
            "total_subnets": total_subnets,
            "total_sgs": total_sgs,
        }

    def discover_infrastructure_interactive(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Discover AWS infrastructure interactively."""
        try:
            from cli.console import print_info, print_error, print_success

            config = provider_config.get("config", {})
            discovery = AWSInfrastructureDiscoveryService(
                region=config.get("region", "us-east-1"),
                profile=config.get("profile", "default")
            )
            
            print_info("Discovering infrastructure...")
            discovered = {}
            
            # Discover VPCs
            vpcs = discovery.discover_vpcs()
            if not vpcs:
                print_info("No VPCs found, skipping infrastructure discovery")
                return {}
            
            print_info("")
            print_info("Found VPCs:")
            for i, vpc in enumerate(vpcs, 1):
                print_info(f"  ({i}) {vpc}")
            
            vpc_choice = input(f"\nSelect VPC (1): ").strip() or "1"
            try:
                selected_vpc = vpcs[int(vpc_choice) - 1]
            except (ValueError, IndexError):
                print_error("Invalid VPC selection, skipping infrastructure discovery")
                return {}
            
            # Discover subnets
            subnets = discovery.discover_subnets(selected_vpc.id)
            if subnets:
                print_info("")
                print_info(f"Found subnets in {selected_vpc.id}:")
                for i, subnet in enumerate(subnets, 1):
                    print_info(f"  ({i}) {subnet}")
                print_info("  (s) Skip subnet selection")
                
                subnet_choice = input(f"\nSelect subnets (comma-separated) (1,2): ").strip()
                if subnet_choice.lower() != 's':
                    if not subnet_choice:
                        subnet_choice = "1,2" if len(subnets) >= 2 else "1"
                    
                    try:
                        subnet_indices = [int(x.strip()) - 1 for x in subnet_choice.split(',')]
                        selected_subnets = [subnets[i] for i in subnet_indices if 0 <= i < len(subnets)]
                        if selected_subnets:
                            discovered["subnet_ids"] = [s.id for s in selected_subnets]
                    except (ValueError, IndexError):
                        print_error("Invalid subnet selection, skipping subnets")
            
            # Discover security groups
            sgs = discovery.discover_security_groups(selected_vpc.id)
            if sgs:
                print_info("")
                print_info(f"Found security groups in {selected_vpc.id}:")
                for i, sg in enumerate(sgs, 1):
                    print_info(f"  ({i}) {sg}")
                print_info("  (s) Skip security group selection")
                
                sg_choice = input(f"\nSelect security groups (1): ").strip() or "1"
                if sg_choice.lower() != 's':
                    try:
                        sg_indices = [int(x.strip()) - 1 for x in sg_choice.split(',')]
                        selected_sgs = [sgs[i] for i in sg_indices if 0 <= i < len(sgs)]
                        if selected_sgs:
                            discovered["security_group_ids"] = [sg.id for sg in selected_sgs]
                    except (ValueError, IndexError):
                        print_error("Invalid security group selection, skipping security groups")
            
            if discovered:
                print_info("")
                print_success("Infrastructure discovered and configured!")
            else:
                print_info("No infrastructure selected")
            
            return discovered
            
        except Exception as e:
            from cli.console import print_error
            print_error(f"Failed to discover infrastructure: {e}")
            print_info("Continuing without infrastructure discovery...")
            return {}

    def validate_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Validate AWS infrastructure configuration."""
        try:
            from cli.console import print_info, print_success, print_error

            config = provider_config.get("config", {})
            template_defaults = provider_config.get("template_defaults", {})
            
            if not template_defaults:
                print_info(f"Provider {provider_config.get('name', 'unknown')}: No infrastructure defaults configured")
                return {
                    "provider": provider_config.get("name", "unknown"), 
                    "status": "no_infrastructure_configured",
                    "message": "No infrastructure defaults to validate.",
                }

            discovery = AWSInfrastructureDiscoveryService(
                region=config.get("region", "us-east-1"),
                profile=config.get("profile", "default")
            )

            validation_results = {"provider": provider_config.get("name", "unknown"), "valid": True, "issues": []}

            # Validate subnets
            if "subnet_ids" in template_defaults:
                try:
                    response = discovery.ec2_client.describe_subnets(SubnetIds=template_defaults["subnet_ids"])
                    print_success(f"Provider {provider_config.get('name', 'unknown')}: All {len(response['Subnets'])} subnets are valid")
                except Exception as e:
                    validation_results["valid"] = False
                    validation_results["issues"].append(f"Invalid subnets: {e}")
                    print_error(f"Provider {provider_config.get('name', 'unknown')}: Subnet validation failed: {e}")

            # Validate security groups
            if "security_group_ids" in template_defaults:
                try:
                    response = discovery.ec2_client.describe_security_groups(GroupIds=template_defaults["security_group_ids"])
                    print_success(f"Provider {provider_config.get('name', 'unknown')}: All {len(response['SecurityGroups'])} security groups are valid")
                except Exception as e:
                    validation_results["valid"] = False
                    validation_results["issues"].append(f"Invalid security groups: {e}")
                    print_error(f"Provider {provider_config.get('name', 'unknown')}: Security group validation failed: {e}")

            return validation_results

        except Exception as e:
            from cli.console import print_error
            print_error(f"Failed to validate infrastructure: {e}")
            return {"provider": provider_config.get("name", "unknown"), "error": str(e)}

    # Credential and provider management methods
    def get_available_credential_sources(self) -> list[dict]:
        """Get available AWS credential sources."""
        from providers.aws.profile_discovery import get_available_profiles
        return get_available_profiles()

    def test_credentials(self, credential_source: Optional[str] = None, **kwargs) -> dict:
        """Test AWS credentials."""
        from providers.aws.session_factory import AWSSessionFactory
        region = kwargs.get("region")
        return AWSSessionFactory.discover_credentials(credential_source, region)

    def get_credential_requirements(self) -> dict:
        """AWS requires region."""
        return {"region": {"required": True, "description": "AWS region"}}

    def generate_provider_name(self, config: dict[str, Any]) -> str:
        """Generate AWS provider name: {provider_type}_{profile}_{region}"""
        provider_type = self.provider_type
        profile = config.get("profile", "default")
        region = config.get("region", "us-east-1")
        
        import re
        sanitized_profile = re.sub(r'[^a-zA-Z0-9\-_]', '-', profile)
        
        return f"{provider_type}_{sanitized_profile}_{region}"

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        """Parse AWS provider name back to components."""
        if "_" in provider_name:
            parts = provider_name.split("_")
            return {
                "type": parts[0] if len(parts) > 0 else self.provider_type,
                "profile": parts[1] if len(parts) > 1 else "default",
                "region": parts[2] if len(parts) > 2 else "us-east-1",
            }
        else:
            return {
                "type": provider_name.split("-")[0],
                "profile": "default",
                "region": "us-east-1",
            }

    def get_provider_name_pattern(self) -> str:
        """AWS naming pattern."""
        return "{type}_{profile}_{region}"

    def cleanup(self) -> None:
        """Clean up AWS provider resources."""
        try:
            if self.aws_client:
                self.aws_client.cleanup()
                self._logger.debug("AWS client cleaned up")

            self._aws_client = None
            self._initialized = False

        except Exception as e:
            self._logger.warning("Failed during AWS provider cleanup: %s", e)

    def __str__(self) -> str:
        """Return string representation for debugging."""
        return f"AWSProviderStrategy(region={self._aws_config.region}, initialized={self._initialized})"

    def __repr__(self) -> str:
        """Return detailed representation for debugging."""
        return (
            f"AWSProviderStrategy("
            f"region={self._aws_config.region}, "
            f"profile={self._aws_config.profile}, "
            f"initialized={self._initialized}"
            f")"
        )

    def generate_provider_name(self, config: dict[str, Any]) -> str:
        """Generate provider name based on AWS-specific components."""
        profile = config.get("profile", "default")
        region = config.get("region", "us-east-1")
        return f"aws_{profile}_{region}"

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        """Parse AWS provider name back to components."""
        parts = provider_name.split("_")
        if len(parts) >= 3 and parts[0] == "aws":
            return {
                "type": "aws",
                "profile": parts[1],
                "region": "_".join(parts[2:])  # Handle regions with underscores
            }
        return {"type": "aws", "profile": "default", "region": "us-east-1"}

    def get_provider_name_pattern(self) -> str:
        """Get the naming pattern for AWS providers."""
        return "aws_{profile}_{region}"

    def get_available_credential_sources(self) -> list[dict]:
        """Get available AWS credential sources."""
        return [
            {"name": "default", "description": "Default AWS credentials"},
            {"name": "profile", "description": "Named AWS profile"},
            {"name": "environment", "description": "Environment variables"},
            {"name": "instance", "description": "EC2 instance profile"},
        ]

    def test_credentials(self, credential_source: Optional[str] = None, **kwargs) -> dict:
        """Test AWS credentials and return metadata."""
        try:
            aws_client = self._get_aws_client()
            if aws_client:
                # Test credentials by calling STS get-caller-identity
                identity = aws_client.get_caller_identity()
                return {
                    "success": True,
                    "account": identity.get("Account"),
                    "user_id": identity.get("UserId"),
                    "arn": identity.get("Arn"),
                    "region": self._config.region,
                    "profile": self._config.profile
                }
        except Exception as e:
            return {"success": False, "error": str(e)}
        
        return {"success": False, "error": "No AWS client available"}

    def get_credential_requirements(self) -> dict:
        """Get required credential parameters for AWS."""
        return {
            "region": {"required": True, "description": "AWS region"},
            "profile": {"required": False, "description": "AWS profile name", "default": "default"}
        }

    # Compatibility methods for existing integrations
    def get_supported_apis(self) -> list[str]:
        """Get supported APIs from handler registry."""
        return list(self._get_handler_registry().get_available_handlers().keys())

    def get_available_credential_sources(self) -> list[dict]:
        """Get available AWS credential sources."""
        from providers.aws.profile_discovery import get_available_profiles
        return get_available_profiles()

    def test_credentials(self, credential_source: Optional[str] = None, **kwargs) -> dict:
        """Test AWS credentials."""
        from providers.aws.session_factory import AWSSessionFactory
        region = kwargs.get("region")
        return AWSSessionFactory.discover_credentials(credential_source, region)

    def get_credential_requirements(self) -> dict:
        """AWS requires region."""
        return {"region": {"required": True, "description": "AWS region"}}

    def generate_provider_name(self, config: dict[str, Any]) -> str:
        """Generate AWS provider name: {provider_type}_{profile}_{region}"""
        provider_type = self.provider_type
        profile = config.get("profile", "default")
        region = config.get("region", "us-east-1")
        
        import re
        sanitized_profile = re.sub(r'[^a-zA-Z0-9\-_]', '-', profile)
        return f"{provider_type}_{sanitized_profile}_{region}"

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        """Parse AWS provider name back to components."""
        if "_" in provider_name:
            parts = provider_name.split("_")
            return {
                "type": parts[0] if len(parts) > 0 else self.provider_type,
                "profile": parts[1] if len(parts) > 1 else "default",
                "region": parts[2] if len(parts) > 2 else "us-east-1",
            }
        else:
            return {
                "type": provider_name.split("-")[0],
                "profile": "default",
                "region": "us-east-1",
            }

    def get_provider_name_pattern(self) -> str:
        """AWS naming pattern."""
        return "{type}_{profile}_{region}"

    def __str__(self) -> str:
        """Return string representation for debugging."""
        return f"AWSProviderStrategy(region={self._config.region}, initialized={self._initialized})"

    def __repr__(self) -> str:
        """Return detailed representation for debugging."""
        return (
            f"AWSProviderStrategy("
            f"region={self._config.region}, "
            f"profile={self._config.profile}, "
            f"initialized={self._initialized}"
            f")"
        )