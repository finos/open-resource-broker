"""AWS Provider Strategy - Strategy pattern implementation for AWS provider.

This module implements the ProviderStrategy interface for AWS cloud provider,
enabling AWS operations to be executed through the strategy pattern while
maintaining all existing AWS functionality and adding new capabilities.
"""

import time
from typing import Any, Dict, List, Optional

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort

# Import AWS-specific components
from providers.aws.configuration.config import AWSProviderConfig
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.infrastructure.handlers.ec2_fleet_handler import EC2FleetHandler
from providers.aws.infrastructure.handlers.run_instances_handler import (
    RunInstancesHandler,
)
from providers.aws.infrastructure.handlers.spot_fleet_handler import SpotFleetHandler
from providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
)
from providers.aws.managers.aws_resource_manager import AWSResourceManager

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

    This class adapts the existing AWS provider functionality to work with
    the strategy pattern, enabling runtime provider switching and composition
    while preserving all AWS-specific capabilities and optimizations.

    Features:
    - Full AWS provider functionality through strategy interface
    - Health monitoring and capability reporting
    - Performance metrics and error handling
    - Resource and instance management integration
    - AWS-specific optimizations and features
    """

    def __init__(self, config: AWSProviderConfig, logger: LoggingPort) -> None:
        """
        Initialize AWS provider strategy.

        Args:
            config: AWS-specific configuration
            logger: Logger for logging messages

        Raises:
            ValueError: If configuration is invalid
        """
        if not isinstance(config, AWSProviderConfig):
            raise ValueError("AWSProviderStrategy requires AWSProviderConfig")

        super().__init__(config)
        self._logger = logger
        self._aws_config = config
        self._aws_client: Optional[AWSClient] = None
        self._resource_manager: Optional[AWSResourceManager] = None
        self._launch_template_manager: Optional[AWSLaunchTemplateManager] = None
        self._handlers: Dict[str, Any] = {}

    @property
    def provider_type(self) -> str:
        """Get the provider type identifier."""
        return "aws"

    @property
    def aws_client(self) -> Optional[AWSClient]:
        """Get the AWS client instance with lazy initialization."""
        if self._aws_client is None:
            self._logger.debug("Creating AWS client on first access")

            # Create a configuration port that provides the instance-specific AWS config
            class AWSInstanceConfigPort:
                """Configuration port that provides instance-specific AWS configuration."""

                def __init__(self, aws_config: AWSProviderConfig) -> None:
                    self._aws_config = aws_config

                def get_typed(self, config_type):
                    """Return the instance-specific AWS config."""
                    if config_type == AWSProviderConfig:
                        return self._aws_config
                    return None

            config_port = AWSInstanceConfigPort(self._aws_config)
            self._aws_client = AWSClient(config=config_port, logger=self._logger)
        return self._aws_client

    @property
    def resource_manager(self) -> Optional[AWSResourceManager]:
        """Get the AWS resource manager with lazy initialization."""
        if self._resource_manager is None and self.aws_client:
            self._logger.debug("Creating AWS resource manager on first access")
            self._resource_manager = AWSResourceManager(
                aws_client=self.aws_client, config=self._aws_config, logger=self._logger
            )
        return self._resource_manager

    @property
    def launch_template_manager(self) -> Optional[AWSLaunchTemplateManager]:
        """Get the AWS launch template manager with lazy initialization."""
        if self._launch_template_manager is None and self.aws_client:
            self._logger.debug("Creating AWS launch template manager on first access")
            self._launch_template_manager = AWSLaunchTemplateManager(
                aws_client=self.aws_client, logger=self._logger
            )
        return self._launch_template_manager

    @property
    def handlers(self) -> Dict[str, Any]:
        """Get the AWS handlers with lazy initialization."""
        if not self._handlers and self.aws_client:
            self._logger.debug("Creating AWS handlers on first access")

            # Initialize AWS operations utility
            from providers.aws.utilities.aws_operations import AWSOperations

            aws_ops = AWSOperations(self.aws_client, self._logger)

            # Initialize handlers with launch template manager
            self._handlers = {
                "SpotFleet": SpotFleetHandler(
                    aws_client=self.aws_client,
                    logger=self._logger,
                    aws_ops=aws_ops,
                    launch_template_manager=self.launch_template_manager,
                ),
                "EC2Fleet": EC2FleetHandler(
                    aws_client=self.aws_client,
                    logger=self._logger,
                    aws_ops=aws_ops,
                    launch_template_manager=self.launch_template_manager,
                ),
                "RunInstances": RunInstancesHandler(
                    aws_client=self.aws_client,
                    logger=self._logger,
                    aws_ops=aws_ops,
                    launch_template_manager=self.launch_template_manager,
                ),
            }
        return self._handlers

    def initialize(self) -> bool:
        """
        Initialize the AWS provider strategy without creating AWS client.

        The AWS client and related components will be created lazily on first use.

        Returns:
            True if initialization successful, False otherwise
        """
        try:
            self._logger.info("AWS provider strategy ready for region: %s", self._aws_config.region)

            # Don't create AWS client here - let it be lazy
            # Don't create managers here - they depend on AWS client
            # Don't create handlers here - they depend on AWS client and managers
            # Don't perform health check here - it would trigger AWS client creation

            self._initialized = True
            self._logger.debug("AWS provider strategy initialized successfully (lazy mode)")
            return True

        except Exception as e:
            self._logger.error("Failed to initialize AWS provider strategy: %s", e)
            return False

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """
        Execute a provider operation using AWS services.

        Args:
            operation: The operation to execute

        Returns:
            Result of the operation execution
        """
        if not self._initialized:
            return ProviderResult.error_result(
                "AWS provider strategy not initialized", "NOT_INITIALIZED"
            )

        start_time = time.time()

        # Check for dry-run context
        is_dry_run = bool(operation.context and operation.context.get("dry_run", False))

        try:
            # Import dry-run context here to avoid circular imports
            from providers.aws.infrastructure.dry_run_adapter import aws_dry_run_context

            # Execute operation within appropriate context
            if is_dry_run:
                with aws_dry_run_context():
                    result = await self._execute_operation_internal(operation)
            else:
                result = await self._execute_operation_internal(operation)

            # Add execution metadata
            execution_time_ms = int((time.time() - start_time) * 1000)

            # Ensure metadata is a mutable dict
            if result.metadata is None:
                result.metadata = {}

            # Update metadata with execution info
            result.metadata.update(
                {
                    "execution_time_ms": execution_time_ms,
                    "provider": "aws",
                    "dry_run": is_dry_run,
                }
            )

            return result

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            self._logger.error("AWS operation failed: %s", e)
            return ProviderResult.error_result(
                f"AWS operation failed: {str(e)}",
                "OPERATION_FAILED",
                {
                    "execution_time_ms": execution_time_ms,
                    "provider": "aws",
                    "dry_run": is_dry_run,
                },
            )

    async def _execute_operation_internal(self, operation: ProviderOperation) -> ProviderResult:
        """
        Execute operations - separated for dry-run context wrapping.

        Args:
            operation: The operation to execute

        Returns:
            Result of the operation execution
        """
        # Route operation to appropriate handler
        if operation.operation_type == ProviderOperationType.CREATE_INSTANCES:
            return self._handle_create_instances(operation)
        elif operation.operation_type == ProviderOperationType.TERMINATE_INSTANCES:
            return self._handle_terminate_instances(operation)
        elif operation.operation_type == ProviderOperationType.GET_INSTANCE_STATUS:
            return self._handle_get_instance_status(operation)
        elif operation.operation_type == ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES:
            return await self._handle_describe_resource_instances(operation)
        elif operation.operation_type == ProviderOperationType.VALIDATE_TEMPLATE:
            return self._handle_validate_template(operation)
        elif operation.operation_type == ProviderOperationType.GET_AVAILABLE_TEMPLATES:
            return self._handle_get_available_templates(operation)
        elif operation.operation_type == ProviderOperationType.HEALTH_CHECK:
            return self._handle_health_check(operation)
        else:
            return ProviderResult.error_result(
                f"Unsupported operation: {operation.operation_type}",
                "UNSUPPORTED_OPERATION",
            )

    def _handle_create_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Handle instance creation operation using handler system."""
        try:
            template_config = operation.parameters.get("template_config", {})
            count = operation.parameters.get("count", 1)

            if not template_config:
                return ProviderResult.error_result(
                    "Template configuration is required for instance creation",
                    "MISSING_TEMPLATE_CONFIG",
                )

            # Determine which handler to use based on provider_api
            provider_api = template_config.get("provider_api", "RunInstances")

            # Get the appropriate handler
            handler = self.handlers.get(provider_api)
            if not handler:
                # Fallback to RunInstances if provider_api not found
                handler = self.handlers.get("RunInstances")
                if not handler:
                    return ProviderResult.error_result(
                        f"No handler available for provider_api: {provider_api}",
                        "HANDLER_NOT_FOUND",
                    )
                self._logger.warning(
                    "Handler for %s not found, using RunInstances fallback", provider_api
                )

            # Convert template_config to AWSTemplate domain object
            from providers.aws.domain.template.aggregate import AWSTemplate

            try:
                aws_template = AWSTemplate.model_validate(template_config)
            except Exception as e:
                self._logger.error("Failed to create AWSTemplate from config: %s", e)
                # Fallback: create minimal AWSTemplate with required fields
                aws_template = AWSTemplate(
                    template_id=template_config.get("template_id", "unknown"),
                    image_id=template_config.get("image_id", ""),
                    instance_type=template_config.get("instance_type", "t2.micro"),
                    subnet_ids=template_config.get("subnet_ids", []),
                    security_group_ids=template_config.get("security_group_ids", []),
                )

            # Create a minimal request object for handler using domain factory
            from domain.request.aggregate import Request
            from domain.request.value_objects import RequestType

            # Use the domain aggregate's factory method - it handles RequestId generation
            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id=aws_template.template_id,
                machine_count=count,
                provider_type="aws",
                provider_instance="aws-default",
            )

            # Use the handler to acquire hosts
            handler_result = handler.acquire_hosts(request, aws_template)

            # Extract resource IDs and instances from handler result
            if isinstance(handler_result, dict):
                # Handler returned structured result
                resource_ids = handler_result.get("resource_ids", [])
                instances = handler_result.get("instances", [])
                success = handler_result.get("success", True)
                error_message = handler_result.get("error_message")

                if not success:
                    return ProviderResult.error_result(
                        error_message or "Handler reported failure", "HANDLER_ERROR"
                    )
            else:
                # Handler returned single resource ID (legacy format)
                resource_ids = [handler_result] if handler_result else []
                instances = []

            self._logger.info(
                "Handler returned resource_ids: %s, instances: %s", resource_ids, len(instances)
            )

            return ProviderResult.success_result(
                {
                    "resource_ids": resource_ids,
                    "instances": instances,
                    "provider_api": provider_api,
                    "count": count,
                    "template_id": aws_template.template_id,
                },
                {
                    "operation": "create_instances",
                    "template_config": template_config,
                    "handler_used": provider_api,
                },
            )

        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to create instances: {str(e)}", "CREATE_INSTANCES_ERROR"
            )

    def _handle_terminate_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Handle instance termination operation."""
        try:
            instance_ids = operation.parameters.get("instance_ids", [])

            if not instance_ids:
                return ProviderResult.error_result(
                    "Instance IDs are required for termination", "MISSING_INSTANCE_IDS"
                )

            # Use AWS client property (with lazy initialization) for termination
            aws_client = self.aws_client
            if not aws_client:
                return ProviderResult.error_result(
                    "AWS client not available", "AWS_CLIENT_NOT_AVAILABLE"
                )

            try:
                response = aws_client.ec2_client.terminate_instances(InstanceIds=instance_ids)
                terminating_count = len(response.get("TerminatingInstances", []))
                success = terminating_count == len(instance_ids)

                return ProviderResult.success_result(
                    {"success": success, "terminated_count": terminating_count},
                    {"operation": "terminate_instances", "instance_ids": instance_ids},
                )

            except Exception as e:
                self._logger.error("Failed to terminate instances: %s", e)
                return ProviderResult.error_result(
                    f"Failed to terminate instances: {str(e)}", "AWS_API_ERROR"
                )

        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to terminate instances: {str(e)}", "TERMINATE_INSTANCES_ERROR"
            )

    def _handle_get_instance_status(self, operation: ProviderOperation) -> ProviderResult:
        """Handle instance status query operation."""
        try:
            instance_ids = operation.parameters.get("instance_ids", [])

            if not instance_ids:
                return ProviderResult.error_result(
                    "Instance IDs are required for status query", "MISSING_INSTANCE_IDS"
                )

            # Use AWS client property (with lazy initialization) for status query
            aws_client = self.aws_client
            if not aws_client:
                return ProviderResult.error_result(
                    "AWS client not available", "AWS_CLIENT_NOT_AVAILABLE"
                )

            try:
                response = aws_client.ec2_client.describe_instances(InstanceIds=instance_ids)

                # Convert AWS instances to domain Machine entities
                machines = []
                for reservation in response["Reservations"]:
                    for aws_instance in reservation["Instances"]:
                        machine = self._convert_aws_instance_to_machine(aws_instance)
                        machines.append(machine)

                return ProviderResult.success_result(
                    {"machines": machines, "queried_count": len(instance_ids)},
                    {"operation": "get_instance_status", "instance_ids": instance_ids},
                )

            except Exception as e:
                self._logger.error("Failed to get instance status: %s", e)
                return ProviderResult.error_result(
                    f"Failed to get instance status: {str(e)}", "AWS_API_ERROR"
                )

        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to get instance status: {str(e)}", "GET_INSTANCE_STATUS_ERROR"
            )

    def _handle_validate_template(self, operation: ProviderOperation) -> ProviderResult:
        """Handle template validation operation."""
        try:
            template_config = operation.parameters.get("template_config", {})

            if not template_config:
                return ProviderResult.error_result(
                    "Template configuration is required for validation",
                    "MISSING_TEMPLATE_CONFIG",
                )

            # Perform AWS-specific template validation
            validation_result = self._validate_aws_template(template_config)

            return ProviderResult.success_result(
                validation_result,
                {"operation": "validate_template", "template_config": template_config},
            )

        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to validate template: {str(e)}", "VALIDATE_TEMPLATE_ERROR"
            )

    def _handle_get_available_templates(self, operation: ProviderOperation) -> ProviderResult:
        """Handle available templates query operation."""
        try:
            # Get available templates from AWS
            templates = self._get_aws_templates()

            return ProviderResult.success_result(
                {"templates": templates, "count": len(templates)},
                {"operation": "get_available_templates"},
            )

        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to get available templates: {str(e)}", "GET_TEMPLATES_ERROR"
            )

    async def _handle_describe_resource_instances(
        self, operation: ProviderOperation
    ) -> ProviderResult:
        """Handle resource-to-instance discovery operation using appropriate handlers."""
        try:
            resource_ids = operation.parameters.get("resource_ids", [])
            provider_api = operation.parameters.get("provider_api", "RunInstances")

            if not resource_ids:
                return ProviderResult.error_result(
                    "Resource IDs are required for instance discovery",
                    "MISSING_RESOURCE_IDS",
                )

            # Get the appropriate handler based on provider_api
            handler = self.handlers.get(provider_api)
            if not handler:
                # Fallback to RunInstances handler
                handler = self.handlers.get("RunInstances")
                if not handler:
                    return ProviderResult.error_result(
                        f"No handler available for provider_api: {provider_api}",
                        "HANDLER_NOT_FOUND",
                    )
                self._logger.warning(
                    "Handler for %s not found, using RunInstances fallback", provider_api
                )

            # Create a minimal request object for the handler
            from domain.request.aggregate import Request
            from domain.request.value_objects import RequestType

            # Create request with the resource IDs
            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id=operation.parameters.get("template_id", "unknown"),
                machine_count=1,
                provider_type="aws",
                provider_instance="aws-default",
            )

            # Set the resource IDs in the request
            request.resource_ids = resource_ids

            # Use the handler's check_hosts_status method for resource-to-instance
            # discovery
            instance_details = await handler.check_hosts_status(request)

            if not instance_details:
                self._logger.info("No instances found for resources: %s", resource_ids)
                return ProviderResult.success_result(
                    {"instances": []},
                    {
                        "operation": "describe_resource_instances",
                        "resource_ids": resource_ids,
                    },
                )

            # Format instance details for consistent output
            formatted_instances = []
            for instance_data in instance_details:
                formatted_instance = {
                    "InstanceId": instance_data.get("InstanceId"),
                    "State": instance_data.get("State", "unknown"),
                    "PrivateIpAddress": instance_data.get("PrivateIpAddress"),
                    "PublicIpAddress": instance_data.get("PublicIpAddress"),
                    "LaunchTime": instance_data.get("LaunchTime"),
                    "InstanceType": instance_data.get("InstanceType"),
                    "SubnetId": instance_data.get("SubnetId"),
                    "VpcId": instance_data.get("VpcId"),
                }
                formatted_instances.append(formatted_instance)

            return ProviderResult.success_result(
                {"instances": formatted_instances},
                {
                    "operation": "describe_resource_instances",
                    "resource_ids": resource_ids,
                    "provider_api": provider_api,
                    "handler_used": provider_api,
                    "instance_count": len(formatted_instances),
                },
            )

        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to describe resource instances: {str(e)}",
                "DESCRIBE_RESOURCE_INSTANCES_ERROR",
            )

    def _handle_health_check(self, operation: ProviderOperation) -> ProviderResult:
        """Handle health check operation."""
        health_status = self.check_health()

        return ProviderResult.success_result(
            {
                "is_healthy": health_status.is_healthy,
                "status_message": health_status.status_message,
                "response_time_ms": health_status.response_time_ms,
            },
            {"operation": "health_check"},
        )

    def get_capabilities(self) -> ProviderCapabilities:
        """
        Get AWS provider capabilities and features.

        Returns:
            Comprehensive capabilities information for AWS provider
        """
        return ProviderCapabilities(
            provider_type="aws",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
                ProviderOperationType.VALIDATE_TEMPLATE,
                ProviderOperationType.GET_AVAILABLE_TEMPLATES,
                ProviderOperationType.HEALTH_CHECK,
            ],
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
                "instance_types": [
                    "t3.micro",
                    "t3.small",
                    "t3.medium",
                    "m5.large",
                    "c5.large",
                ],
                "max_instances_per_request": 1000,
                "supports_windows": True,
                "supports_linux": True,
            },
            limitations={
                "max_concurrent_requests": 100,
                "rate_limit_per_second": 10,
                "max_instance_lifetime_hours": 8760,  # 1 year
                "requires_vpc": False,
                "requires_key_pair": False,
            },
            performance_metrics={
                "typical_create_time_seconds": 60,
                "typical_terminate_time_seconds": 30,
                "health_check_timeout_seconds": 10,
            },
        )

    def check_health(self) -> ProviderHealthStatus:
        """
        Check the health status of AWS provider.

        Performs connectivity and service availability checks.
        Respects dry-run context to avoid real AWS calls during testing.

        Returns:
            Current health status of the AWS provider
        """
        start_time = time.time()

        try:
            if not self._aws_client:
                return ProviderHealthStatus.unhealthy(
                    "AWS client not initialized", {"error": "client_not_initialized"}
                )

            # Check if we're in dry-run mode
            from infrastructure.mocking.dry_run_context import is_dry_run_active

            if is_dry_run_active():
                # In dry-run mode, return a healthy status without making real AWS calls
                response_time_ms = (time.time() - start_time) * 1000
                return ProviderHealthStatus.healthy(
                    f"AWS provider healthy (DRY-RUN) - Region: {self._aws_config.region}",
                    response_time_ms,
                )

            # Perform basic AWS connectivity check
            # This is a lightweight operation to verify AWS access
            try:
                # Import dry-run context here to avoid circular imports
                from providers.aws.infrastructure.dry_run_adapter import (
                    aws_dry_run_context,
                )

                with aws_dry_run_context():
                    # Simple STS call to verify credentials and connectivity
                    response = self._aws_client.sts_client.get_caller_identity()
                    account_id = response.get("Account", "unknown")

                    response_time_ms = (time.time() - start_time) * 1000

                    return ProviderHealthStatus.healthy(
                        f"AWS provider healthy - Account: {account_id}, Region: {self._aws_config.region}",
                        response_time_ms,
                    )

            except Exception as e:
                response_time_ms = (time.time() - start_time) * 1000
                return ProviderHealthStatus.unhealthy(
                    f"AWS connectivity check failed: {str(e)}",
                    {
                        "error": str(e),
                        "region": self._aws_config.region,
                        "response_time_ms": response_time_ms,
                    },
                )

        except Exception as e:
            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.unhealthy(
                f"Health check error: {str(e)}",
                {"error": str(e), "response_time_ms": response_time_ms},
            )

    def _validate_aws_template(self, template_config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate AWS-specific template configuration."""
        validation_errors = []
        validation_warnings = []

        # Required fields validation
        required_fields = ["image_id", "instance_type"]
        for field in required_fields:
            if field not in template_config:
                validation_errors.append(f"Missing required field: {field}")

        # AWS-specific validations
        if "image_id" in template_config:
            image_id = template_config["image_id"]
            if not image_id.startswith("ami-"):
                validation_errors.append(f"Invalid AMI ID format: {image_id}")

        if "instance_type" in template_config:
            instance_type = template_config["instance_type"]
            # Basic instance type validation
            if not any(
                instance_type.startswith(prefix) for prefix in ["t3.", "t2.", "m5.", "c5.", "r5."]
            ):
                validation_warnings.append(f"Uncommon instance type: {instance_type}")

        return {
            "valid": len(validation_errors) == 0,
            "errors": validation_errors,
            "warnings": validation_warnings,
            "validated_fields": list(template_config.keys()),
        }

    def _get_aws_templates(self) -> List[Dict[str, Any]]:
        """Get available AWS templates using scheduler strategy."""
        try:
            # Use scheduler strategy to load templates from configuration
            from infrastructure.registry.scheduler_registry import (
                get_scheduler_registry,
            )

            scheduler_registry = get_scheduler_registry()
            scheduler_strategy = scheduler_registry.get_active_strategy()

            if scheduler_strategy:
                # Get template paths from scheduler strategy
                template_paths = scheduler_strategy.get_template_paths()

                # Load templates using scheduler strategy
                templates = []
                for template_path in template_paths:
                    try:
                        template_data = scheduler_strategy.load_templates_from_path(template_path)
                        templates.extend(template_data)
                    except Exception as e:
                        self._logger.warning(
                            "Failed to load templates from %s: %s", template_path, e
                        )

                return templates
            else:
                self._logger.warning("No scheduler strategy available, using fallback templates")
                # Fallback to basic templates if no scheduler strategy
                return self._get_fallback_templates()

        except Exception as e:
            self._logger.error("Failed to load templates via scheduler strategy: %s", e)
            return self._get_fallback_templates()

    def _get_fallback_templates(self) -> List[Dict[str, Any]]:
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

    def _convert_aws_instance_to_machine(self, aws_instance: Dict[str, Any]) -> Dict[str, Any]:
        """Convert AWS instance data to domain Machine entity data.

        This method translates AWS-specific field names and formats to domain entity format,
        ensuring the application layer only works with domain objects.
        """
        from domain.machine.machine_status import MachineStatus

        # Extract AWS state information
        aws_state = aws_instance.get("State", {})
        if isinstance(aws_state, dict):
            state_name = aws_state.get("Name", "unknown")
        else:
            state_name = str(aws_state)

        # Map AWS state to domain MachineStatus
        status_mapping = {
            "pending": MachineStatus.PENDING,
            "running": MachineStatus.RUNNING,
            "shutting-down": MachineStatus.SHUTTING_DOWN,
            "terminated": MachineStatus.TERMINATED,
            "stopping": MachineStatus.STOPPING,
            "stopped": MachineStatus.STOPPED,
        }
        machine_status = status_mapping.get(state_name, MachineStatus.UNKNOWN)

        # Convert to domain entity format (no AWS-specific field names)
        return {
            "instance_id": aws_instance.get("InstanceId"),
            "status": machine_status.value,
            "private_ip": aws_instance.get("PrivateIpAddress"),
            "public_ip": aws_instance.get("PublicIpAddress"),
            "launch_time": aws_instance.get("LaunchTime"),
            "instance_type": aws_instance.get("InstanceType"),
            "subnet_id": aws_instance.get("SubnetId"),
            "vpc_id": aws_instance.get("VpcId"),
            "availability_zone": aws_instance.get("Placement", {}).get("AvailabilityZone"),
            "provider_type": "aws",
            "provider_data": {
                "aws_instance_id": aws_instance.get("InstanceId"),
                "aws_state": aws_state,
                "aws_placement": aws_instance.get("Placement", {}),
                "aws_security_groups": aws_instance.get("SecurityGroups", []),
                "aws_tags": aws_instance.get("Tags", []),
            },
        }

    def cleanup(self) -> None:
        """Clean up AWS provider resources."""
        try:
            if self._aws_client:
                self._aws_client.cleanup()
                self._logger.debug("AWS client cleaned up")

            self._aws_client = None
            self._resource_manager = None
            self._launch_template_manager = None
            self._handlers = {}
            self._initialized = False

        except Exception as e:
            self._logger.warning("Error during AWS provider cleanup: %s", e)

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
