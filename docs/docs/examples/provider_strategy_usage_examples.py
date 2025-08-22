"""
Practical usage examples for Provider Strategy Pattern.

This module provides real-world examples of how to use the provider strategy pattern
for various scenarios including new provider creation, runtime switching, load balancing,
and production deployment patterns.
"""

import json
import logging
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

from src.providers.base.strategy import (
    AggregationPolicy,
    CompositeProviderStrategy,
    CompositionConfig,
    CompositionMode,
    FallbackConfig,
    FallbackMode,
    FallbackProviderStrategy,
    HealthCheckMode,
    LoadBalancingAlgorithm,
    LoadBalancingConfig,
    LoadBalancingProviderStrategy,
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
    create_provider_context,
)

# =============================================================================
# EXAMPLE 1: CREATING A NEW PROVIDER (Provider1)
# =============================================================================


class Provider1Config:
    """Configuration for Provider1 cloud service."""

    def __init__(self, api_endpoint: str, api_key: str, region: str = "us-east-1"):
        self.api_endpoint = api_endpoint
        self.api_key = api_key
        self.region = region


class Provider1Strategy(ProviderStrategy):
    """
    Example implementation of a new cloud provider strategy.

    This demonstrates how to create a new provider that integrates
    with the strategy pattern system.
    """

    def __init__(self, config: Provider1Config, logger=None):
        """Initialize Provider1Strategy with configuration and logger."""
        from src.infrastructure.interfaces.provider import ProviderConfig

        super().__init__(ProviderConfig(provider_type="provider1"))
        self._config = config
        self._logger = logger
        self._client = None

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier."""
        return "provider1"

    def initialize(self) -> bool:
        """Initialize Provider1 client and connections."""
        try:
            # Simulate Provider1 client initialization
            self._client = self._create_provider1_client()
            self._initialized = True
            self._logger.info(f"Provider1 initialized for region: {self._config.region}")
            return True
        except Exception as e:
            self._logger.error(f"Provider1 initialization failed: {e}")
            return False

    def _create_provider1_client(self):
        """Create Provider1 API client (simulated)."""
        # In real implementation, this would create actual API client
        return {
            "endpoint": self._config.api_endpoint,
            "api_key": self._config.api_key,
            "region": self._config.region,
            "connected": True,
        }

    def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """Execute operations using Provider1 API."""
        if not self._initialized:
            return ProviderResult.error_result("Provider1 not initialized", "NOT_INITIALIZED")

        try:
            if operation.operation_type == ProviderOperationType.CREATE_INSTANCES:
                return self._create_instances(operation)
            elif operation.operation_type == ProviderOperationType.TERMINATE_INSTANCES:
                return self._terminate_instances(operation)
            elif operation.operation_type == ProviderOperationType.GET_INSTANCE_STATUS:
                return self._get_instance_status(operation)
            elif operation.operation_type == ProviderOperationType.HEALTH_CHECK:
                return self._health_check(operation)
            elif operation.operation_type == ProviderOperationType.VALIDATE_TEMPLATE:
                return self._validate_template(operation)
            elif operation.operation_type == ProviderOperationType.GET_AVAILABLE_TEMPLATES:
                return self._get_available_templates(operation)
            else:
                return ProviderResult.error_result(
                    f"Unsupported operation: {operation.operation_type}", "UNSUPPORTED_OPERATION"
                )
        except Exception as e:
            return ProviderResult.error_result(
                f"Provider1 operation failed: {str(e)}", "PROVIDER1_ERROR"
            )

    def _create_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Create instances using Provider1 API."""
        operation.parameters.get("template_config", {})
        count = operation.parameters.get("count", 1)

        # Simulate Provider1 instance creation
        instance_ids = [f"provider1-{i:04d}" for i in range(1, count + 1)]

        return ProviderResult.success_result(
            {
                "instance_ids": instance_ids,
                "count": len(instance_ids),
                "provider": "provider1",
                "region": self._config.region,
            }
        )

    def _terminate_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Terminate instances using Provider1 API."""
        instance_ids = operation.parameters.get("instance_ids", [])

        # Simulate Provider1 instance termination
        return ProviderResult.success_result(
            {
                "terminated_instances": instance_ids,
                "count": len(instance_ids),
                "provider": "provider1",
            }
        )

    def _get_instance_status(self, operation: ProviderOperation) -> ProviderResult:
        """Get instance status using Provider1 API."""
        instance_ids = operation.parameters.get("instance_ids", [])

        # Simulate Provider1 status query
        status_map = {instance_id: "running" for instance_id in instance_ids}

        return ProviderResult.success_result(
            {"instance_status": status_map, "provider": "provider1"}
        )

    def _health_check(self, operation: ProviderOperation) -> ProviderResult:
        """Perform Provider1 health check."""
        # Simulate health check
        return ProviderResult.success_result(
            {
                "is_healthy": True,
                "provider": "provider1",
                "region": self._config.region,
                "api_endpoint": self._config.api_endpoint,
            }
        )

    def _validate_template(self, operation: ProviderOperation) -> ProviderResult:
        """Validate template for Provider1."""
        template_config = operation.parameters.get("template_config", {})

        # Simulate Provider1 template validation
        errors = []
        warnings = []

        if not template_config.get("instance_type"):
            errors.append("instance_type is required")

        if not template_config.get("image_id"):
            errors.append("image_id is required")

        return ProviderResult.success_result(
            {
                "valid": len(errors) == 0,
                "errors": errors,
                "warnings": warnings,
                "provider": "provider1",
            }
        )

    def _get_available_templates(self, operation: ProviderOperation) -> ProviderResult:
        """Get available templates from Provider1."""
        # Simulate Provider1 template listing
        templates = [
            {
                "template_id": "provider1-web-server",
                "name": "Provider1 Web Server",
                "instance_type": "standard.small",
                "image_id": "provider1-img-001",
            },
            {
                "template_id": "provider1-database",
                "name": "Provider1 Database Server",
                "instance_type": "standard.large",
                "image_id": "provider1-img-002",
            },
        ]

        return ProviderResult.success_result(
            {"templates": templates, "count": len(templates), "provider": "provider1"}
        )

    def get_capabilities(self) -> ProviderCapabilities:
        """Get Provider1 capabilities."""
        return ProviderCapabilities(
            provider_type="provider1",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.VALIDATE_TEMPLATE,
                ProviderOperationType.GET_AVAILABLE_TEMPLATES,
                ProviderOperationType.HEALTH_CHECK,
            ],
            features={
                "instance_management": True,
                "auto_scaling": True,
                "load_balancing": False,
                "monitoring": True,
                "regions": ["us-east-1", "us-west-2", "eu-west-1"],
                "instance_types": ["standard.small", "standard.medium", "standard.large"],
                "max_instances_per_request": 50,
                "supports_spot_instances": False,
                "supports_reserved_instances": True,
            },
            limitations={
                "max_concurrent_requests": 20,
                "rate_limit_per_second": 5,
                "max_instance_lifetime_hours": 2160,  # 90 days
            },
            performance_metrics={
                "typical_create_time_seconds": 45,
                "typical_terminate_time_seconds": 20,
                "health_check_timeout_seconds": 5,
            },
        )

    def check_health(self) -> ProviderHealthStatus:
        """Check Provider1 service health."""
        start_time = time.time()

        try:
            if not self._client or not self._client.get("connected"):
                return ProviderHealthStatus.unhealthy("Provider1 client not connected")

            # Simulate health check API call
            time.sleep(0.05)  # Simulate network delay

            response_time_ms = (time.time() - start_time) * 1000

            return ProviderHealthStatus.healthy(
                f"Provider1 healthy - Region: {self._config.region}", response_time_ms
            )

        except Exception as e:
            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.unhealthy(
                f"Provider1 health check failed: {str(e)}",
                {"error": str(e), "response_time_ms": response_time_ms},
            )


# =============================================================================
# EXAMPLE 2: RUNTIME PROVIDER SWITCHING
# =============================================================================


def example_runtime_switching():
    """Demonstrate runtime provider switching."""
    logger.info("Runtime Example: Runtime Provider Switching")
    logger.info("-" * 40)

    # Create provider context
    context = create_provider_context()

    # Create multiple providers
    from src.providers.aws.configuration.config import AWSConfig
    from src.providers.aws.strategy import AWSProviderStrategy

    aws_config = AWSConfig(region="us-east-1", profile="default")
    aws_strategy = AWSProviderStrategy(aws_config)

    provider1_config = Provider1Config(
        api_endpoint="https://api.provider1.com", api_key="test-key-123", region="us-east-1"
    )
    provider1_strategy = Provider1Strategy(provider1_config)

    # Register providers
    context.register_strategy(aws_strategy)
    context.register_strategy(provider1_strategy)

    # Initialize context
    context.initialize()

    logger.info(f"Available providers: {context.available_strategies}")
    logger.info(f"Current provider: {context.current_strategy_type}")

    # Create test operation
    operation = ProviderOperation(
        operation_type=ProviderOperationType.GET_AVAILABLE_TEMPLATES, parameters={}
    )

    # Execute with AWS
    logger.info("\n* Executing with AWS:")
    context.set_strategy("aws")
    result = context.execute_operation(operation)
    logger.info(f"Result: {result.success}, Templates: {len(result.data.get('templates', []))}")

    # Switch to Provider1
    logger.info("\n* Switching to Provider1:")
    context.set_strategy("provider1")
    result = context.execute_operation(operation)
    logger.info(f"Result: {result.success}, Templates: {len(result.data.get('templates', []))}")

    # Show metrics
    logger.info("\nMetrics Metrics:")
    for strategy_type in context.available_strategies:
        metrics = context.get_strategy_metrics(strategy_type)
        logger.info(
            f"  {strategy_type}: {metrics.total_operations} ops, {metrics.success_rate:.1f}% success"
        )


# =============================================================================
# EXAMPLE 3: LOAD BALANCING SETUP
# =============================================================================


def example_load_balancing():
    """Demonstrate load balancing across multiple providers."""
    logger.info("\nLoad Balancing Example: Load Balancing")
    logger.info("-" * 40)

    # Create multiple provider strategies
    aws_config = AWSConfig(region="us-east-1", profile="default")
    aws_strategy = AWSProviderStrategy(aws_config)

    provider1_config = Provider1Config(
        api_endpoint="https://api.provider1.com", api_key="test-key-123"
    )
    provider1_strategy = Provider1Strategy(provider1_config)

    # Create load balancing configuration
    lb_config = LoadBalancingConfig(
        algorithm=LoadBalancingAlgorithm.WEIGHTED_ROUND_ROBIN,
        health_check_mode=HealthCheckMode.HYBRID,
        max_connections_per_strategy=10,
    )

    # Set up weights (AWS gets 70%, Provider1 gets 30%)
    weights = {"aws": 0.7, "provider1": 0.3}

    # Create load balancer
    load_balancer = LoadBalancingProviderStrategy(
        strategies=[aws_strategy, provider1_strategy], weights=weights, config=lb_config
    )

    # Initialize
    load_balancer.initialize()

    logger.info(f"Load balancer created with algorithm: {lb_config.algorithm.value}")
    logger.info(f"Weights: {weights}")

    # Execute multiple operations
    operation = ProviderOperation(operation_type=ProviderOperationType.HEALTH_CHECK, parameters={})

    logger.info("\n* Executing 10 operations:")
    for i in range(10):
        result = load_balancer.execute_operation(operation)
        selected_provider = result.metadata.get("selected_strategy", "unknown")
        logger.info(
            f"  Operation {i + 1}: {selected_provider} ({'PASS' if result.success else 'FAIL'})"
        )

    # Show statistics
    logger.info("\nMetrics Load Balancer Statistics:")
    stats = load_balancer.strategy_stats
    for strategy_type, metrics in stats.items():
        logger.info(f"  {strategy_type}:")
        logger.info(f"    Requests: {metrics['total_requests']}")
        logger.info(f"    Success Rate: {metrics['success_rate']:.1f}%")
        logger.info(f"    Avg Response Time: {metrics['average_response_time']:.1f}ms")
        logger.info(f"    Healthy: {'PASS' if metrics['is_healthy'] else 'FAIL'}")


# =============================================================================
# EXAMPLE 4: FALLBACK AND RESILIENCE
# =============================================================================


def example_fallback_resilience():
    """Demonstrate fallback and resilience patterns."""
    logger.info("\nResilience Example: Fallback and Resilience")
    logger.info("-" * 40)

    # Create primary and fallback strategies
    aws_config = AWSConfig(region="us-east-1", profile="default")
    primary_strategy = AWSProviderStrategy(aws_config)

    provider1_config = Provider1Config(
        api_endpoint="https://api.provider1.com", api_key="test-key-123"
    )
    fallback1_strategy = Provider1Strategy(provider1_config)

    # Create fallback configuration with circuit breaker
    fallback_config = FallbackConfig(
        mode=FallbackMode.CIRCUIT_BREAKER,
        max_retries=2,
        circuit_breaker_threshold=3,
        circuit_breaker_timeout_seconds=30.0,
        enable_graceful_degradation=True,
    )

    # Create fallback strategy
    fallback_strategy = FallbackProviderStrategy(
        primary_strategy=primary_strategy,
        fallback_strategies=[fallback1_strategy],
        config=fallback_config,
    )

    # Initialize
    fallback_strategy.initialize()

    logger.info("Fallback strategy created:")
    logger.info(f"  Mode: {fallback_config.mode.value}")
    logger.info(f"  Primary: {primary_strategy.provider_type}")
    logger.info(f"  Fallbacks: {[s.provider_type for s in fallback_strategy.fallback_strategies]}")

    # Execute operations
    operation = ProviderOperation(operation_type=ProviderOperationType.HEALTH_CHECK, parameters={})

    logger.info("\n* Executing operations with fallback:")
    for i in range(5):
        result = fallback_strategy.execute_operation(operation)
        current_provider = fallback_strategy.current_strategy.provider_type
        logger.info(
            f"  Operation {i + 1}: {current_provider} ({'PASS' if result.success else 'FAIL'})"
        )

    # Show circuit breaker metrics
    logger.info("\nMetrics Circuit Breaker Metrics:")
    metrics = fallback_strategy.circuit_metrics
    logger.info(f"  State: {metrics['state']}")
    logger.info(f"  Total Requests: {metrics['total_requests']}")
    logger.info(f"  Success Rate: {metrics['failure_rate']:.1f}%")
    logger.info(f"  Failure Count: {metrics['failure_count']}")


# =============================================================================
# EXAMPLE 5: MULTI-PROVIDER COMPOSITION
# =============================================================================


def example_multi_provider_composition():
    """Demonstrate multi-provider composition for complex scenarios."""
    logger.info("\nRuntime Example: Multi-Provider Composition")
    logger.info("-" * 40)

    # Create multiple strategies
    aws_config = AWSConfig(region="us-east-1", profile="default")
    aws_strategy = AWSProviderStrategy(aws_config)

    provider1_config = Provider1Config(
        api_endpoint="https://api.provider1.com", api_key="test-key-123"
    )
    provider1_strategy = Provider1Strategy(provider1_config)

    # Create composition configuration for parallel execution
    composition_config = CompositionConfig(
        mode=CompositionMode.PARALLEL,
        aggregation_policy=AggregationPolicy.MERGE_ALL,
        max_concurrent_operations=5,
        timeout_seconds=10.0,
        min_success_count=1,
    )

    # Create composite strategy
    composite_strategy = CompositeProviderStrategy(
        strategies=[aws_strategy, provider1_strategy], config=composition_config
    )

    # Initialize
    composite_strategy.initialize()

    logger.info("Composite strategy created:")
    logger.info(f"  Mode: {composition_config.mode.value}")
    logger.info(f"  Aggregation: {composition_config.aggregation_policy.value}")
    logger.info(f"  Providers: {list(composite_strategy.composed_strategies.keys())}")

    # Execute operation that will run on all providers
    operation = ProviderOperation(
        operation_type=ProviderOperationType.GET_AVAILABLE_TEMPLATES, parameters={}
    )

    logger.info("\n* Executing parallel operation:")
    start_time = time.time()
    result = composite_strategy.execute_operation(operation)
    end_time = time.time()

    logger.info(f"  Result: {'PASS' if result.success else 'FAIL'}")
    logger.info(f"  Execution time: {(end_time - start_time) * 1000:.1f}ms")
    logger.info(f"  Strategies executed: {result.metadata.get('strategies_executed', 0)}")
    logger.info(f"  Successful strategies: {result.metadata.get('successful_strategies', 0)}")

    # Show merged results
    if result.success and isinstance(result.data, dict):
        total_templates = 0
        for key, value in result.data.items():
            if isinstance(value, list):
                total_templates += len(value)
        logger.info(f"  Total templates from all providers: {total_templates}")


# =============================================================================
# EXAMPLE 6: PRODUCTION MONITORING SETUP
# =============================================================================


def example_production_monitoring():
    """Demonstrate production monitoring and alerting setup."""
    logger.info("\nMetrics Example: Production Monitoring")
    logger.info("-" * 40)

    # Create provider context with multiple strategies
    context = create_provider_context()

    aws_config = AWSConfig(region="us-east-1", profile="default")
    aws_strategy = AWSProviderStrategy(aws_config)

    provider1_config = Provider1Config(
        api_endpoint="https://api.provider1.com", api_key="test-key-123"
    )
    provider1_strategy = Provider1Strategy(provider1_config)

    context.register_strategy(aws_strategy)
    context.register_strategy(provider1_strategy)
    context.initialize()

    # Simulate some operations to generate metrics
    operation = ProviderOperation(operation_type=ProviderOperationType.HEALTH_CHECK, parameters={})

    logger.info("* Generating sample metrics...")
    for i in range(10):
        # Alternate between providers
        provider = "aws" if i % 2 == 0 else "provider1"
        context.set_strategy(provider)
        context.execute_operation(operation)

    # Display monitoring dashboard
    logger.info("\nMetrics Provider Monitoring Dashboard:")
    logger.info(f"{'Provider':<12} {'Health':<8} {'Ops':<6} {'Success':<8} {'Avg RT':<8}")
    logger.info("-" * 50)

    for strategy_type in context.available_strategies:
        # Get health status
        health = context.check_strategy_health(strategy_type)
        health_icon = "PASS" if health.is_healthy else "FAIL"

        # Get metrics
        metrics = context.get_strategy_metrics(strategy_type)

        logger.info(
            f"{strategy_type:<12} {health_icon:<8} {metrics.total_operations:<6} "
            f"{metrics.success_rate:<7.1f}% {metrics.average_response_time_ms:<7.1f}ms"
        )

    # Show alerting conditions
    logger.info("\nAlert Alerting Conditions:")
    for strategy_type in context.available_strategies:
        health = context.check_strategy_health(strategy_type)
        metrics = context.get_strategy_metrics(strategy_type)

        alerts = []
        if not health.is_healthy:
            alerts.append("UNHEALTHY")
        if metrics.success_rate < 95.0:
            alerts.append("LOW_SUCCESS_RATE")
        if metrics.average_response_time_ms > 1000:
            alerts.append("HIGH_RESPONSE_TIME")

        if alerts:
            logger.info(f"  Warning  {strategy_type}: {', '.join(alerts)}")
        else:
            logger.info(f"  PASS {strategy_type}: All metrics normal")


# =============================================================================
# EXAMPLE 7: CONFIGURATION-DRIVEN SETUP
# =============================================================================


def example_configuration_driven_setup():
    """Demonstrate configuration-driven provider setup."""
    logger.info("\nConfiguration Example: Configuration-Driven Setup")
    logger.info("-" * 40)

    # Example configuration (would typically be loaded from file)
    config = {
        "provider": {
            "strategy": {
                "selection_policy": "performance_based",
                "health_check_interval_seconds": 30,
                "selection_criteria": {
                    "min_success_rate": 95.0,
                    "max_response_time_ms": 2000,
                    "require_healthy": True,
                },
            },
            "providers": {
                "aws": {"region": "us-east-1", "profile": "default", "enabled": True},
                "provider1": {
                    "api_endpoint": "https://api.provider1.com",
                    "api_key": "test-key-123",
                    "region": "us-east-1",
                    "enabled": True,
                },
            },
            "load_balancing": {
                "enabled": True,
                "algorithm": "weighted_round_robin",
                "weights": {"aws": 0.7, "provider1": 0.3},
            },
            "fallback": {
                "enabled": True,
                "mode": "circuit_breaker",
                "primary": "aws",
                "fallbacks": ["provider1"],
            },
        }
    }

    logger.info("Config Configuration loaded:")
    logger.info(json.dumps(config["provider"], indent=2))

    # Create providers based on configuration
    strategies = []

    if config["provider"]["providers"]["aws"]["enabled"]:
        aws_config = AWSConfig(
            region=config["provider"]["providers"]["aws"]["region"],
            profile=config["provider"]["providers"]["aws"]["profile"],
        )
        aws_strategy = AWSProviderStrategy(aws_config)
        strategies.append(aws_strategy)
        logger.info("PASS AWS provider configured")

    if config["provider"]["providers"]["provider1"]["enabled"]:
        provider1_config = Provider1Config(
            api_endpoint=config["provider"]["providers"]["provider1"]["api_endpoint"],
            api_key=config["provider"]["providers"]["provider1"]["api_key"],
            region=config["provider"]["providers"]["provider1"]["region"],
        )
        provider1_strategy = Provider1Strategy(provider1_config)
        strategies.append(provider1_strategy)
        logger.info("PASS Provider1 configured")

    # Set up load balancing if enabled
    if config["provider"]["load_balancing"]["enabled"]:
        lb_config = LoadBalancingConfig(
            algorithm=LoadBalancingAlgorithm(config["provider"]["load_balancing"]["algorithm"])
        )

        weights = config["provider"]["load_balancing"]["weights"]

        load_balancer = LoadBalancingProviderStrategy(
            strategies=strategies, weights=weights, config=lb_config
        )

        load_balancer.initialize()
        logger.info(f"PASS Load balancing configured with {lb_config.algorithm.value}")

        # Test the configured system
        operation = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK, parameters={}
        )

        result = load_balancer.execute_operation(operation)
        logger.info(f"PASS System test: {'PASS' if result.success else 'FAIL'}")


# =============================================================================
# MAIN EXECUTION
# =============================================================================

if __name__ == "__main__":
    logger.info("Provider Strategy Provider Strategy Pattern - Usage Examples")
    logger.info("=" * 60)

    try:
        # Run all examples
        example_runtime_switching()
        example_load_balancing()
        example_fallback_resilience()
        example_multi_provider_composition()
        example_production_monitoring()
        example_configuration_driven_setup()

        logger.info("\nSuccess All examples completed successfully!")
        logger.info("\nSummary Key Takeaways:")
        logger.info("  • Easy to add new providers by implementing ProviderStrategy")
        logger.info("  • Runtime switching enables dynamic provider selection")
        logger.info("  • Load balancing optimizes performance across providers")
        logger.info("  • Fallback strategies ensure high availability")
        logger.info("  • Composition enables complex multi-provider scenarios")
        logger.info("  • Configuration-driven setup simplifies deployment")

    except Exception as e:
        logger.info(f"\nFAIL Example execution failed: {e}")
        import traceback

        traceback.print_exc()
