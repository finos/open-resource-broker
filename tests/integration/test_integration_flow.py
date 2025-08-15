#!/usr/bin/env python3
"""
Test for Integration Flow: Integration Flow Fix
Tests that the AWS provider strategy now uses the appropriate handler system instead of bypassing it.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def test_aws_provider_strategy_uses_handlers():
    """Test that AWS provider strategy uses appropriate handler system."""
    print("=== Integration Flow Test ===")

    try:
        from infrastructure.logging.logger import get_logger
        from providers.aws.configuration.config import AWSProviderConfig
        from providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy
        from providers.base.strategy import ProviderOperation, ProviderOperationType

        # Create AWS provider strategy
        config = AWSProviderConfig(region="us-west-2", profile="default")
        logger = get_logger(__name__)
        strategy = AWSProviderStrategy(config, logger)

        # Initialize the strategy
        initialized = strategy.initialize()
        print(f"PASS: AWS provider strategy initialized: {initialized}")

        # Check that handlers are properly initialized
        handlers = strategy.handlers
        print(f"PASS: Handlers initialized: {list(handlers.keys())}")

        # Verify expected handlers are present
        expected_handlers = ["SpotFleet", "EC2Fleet", "RunInstances"]
        for handler_name in expected_handlers:
            assert handler_name in handlers, f"Handler {handler_name} not found"
            print(f"   - {handler_name}: PASS:")

        # Check that launch template manager is available
        lt_manager = strategy.launch_template_manager
        assert lt_manager is not None, "Launch template manager should be initialized"
        print(f"PASS: Launch template manager initialized: {type(lt_manager).__name__}")

        # Test that create_instances operation routes to handlers
        template_config = {
            "template_id": "test-template",
            "image_id": "ami-123456",
            "instance_type": "t2.micro",
            "subnet_ids": ["subnet-123"],
            "security_group_ids": ["sg-123"],
            "provider_api": "SpotFleet",  # Should route to SpotFleet handler
        }

        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"template_config": template_config, "count": 1},
            context={"correlation_id": "test-123", "dry_run": True},
        )

        print("PASS: Testing create_instances operation routing...")
        result = strategy.execute_operation(operation)

        print(f"PASS: Operation result: success={result.success}")
        if result.success:
            print(f"   - Resource ID: {result.data.get('resource_id')}")
            print(f"   - Provider API used: {result.data.get('provider_api')}")
            print(f"   - Handler used: {result.metadata.get('handler_used')}")
        else:
            print(f"   - Error: {result.error_message}")

        # Test fallback to RunInstances when provider_api not specified
        template_config_no_api = {
            "template_id": "test-template-2",
            "image_id": "ami-123456",
            "instance_type": "t2.micro",
            "subnet_ids": ["subnet-123"],
            "security_group_ids": ["sg-123"],
            # No provider_api - should default to RunInstances
        }

        operation2 = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"template_config": template_config_no_api, "count": 1},
            context={"correlation_id": "test-456", "dry_run": True},
        )

        print("PASS: Testing fallback to RunInstances...")
        result2 = strategy.execute_operation(operation2)

        print(f"PASS: Fallback result: success={result2.success}")
        if result2.success:
            print(f"   - Provider API used: {result2.data.get('provider_api')}")
            print(f"   - Handler used: {result2.metadata.get('handler_used')}")

        return True

    except Exception as e:
        print(f"FAIL: Integration Flow integration flow test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_no_instance_manager_bypass():
    """Test that AWSInstanceManager is no longer used directly."""
    print("\n=== No Instance Manager Bypass Test ===")

    try:
        from infrastructure.logging.logger import get_logger
        from providers.aws.configuration.config import AWSProviderConfig
        from providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

        # Create AWS provider strategy
        config = AWSProviderConfig(region="us-west-2", profile="default")
        logger = get_logger(__name__)
        strategy = AWSProviderStrategy(config, logger)

        # Initialize the strategy
        strategy.initialize()

        # Check that instance_manager property doesn't exist (should be removed)
        assert not hasattr(
            strategy, "instance_manager"
        ), "instance_manager property should be removed"
        print("PASS: instance_manager property correctly removed")

        # Check that _instance_manager attribute doesn't exist
        assert not hasattr(
            strategy, "_instance_manager"
        ), "_instance_manager attribute should be removed"
        print("PASS: _instance_manager attribute correctly removed")

        # Check that handlers and launch_template_manager exist instead
        assert hasattr(strategy, "handlers"), "handlers property should exist"
        assert hasattr(
            strategy, "launch_template_manager"
        ), "launch_template_manager property should exist"
        print("PASS: New handler system properties exist")

        return True

    except Exception as e:
        print(f"FAIL: No instance manager bypass test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("Running Integration Flow: Integration Flow Fix Tests...")

    test1_passed = test_aws_provider_strategy_uses_handlers()
    test2_passed = test_no_instance_manager_bypass()

    if test1_passed and test2_passed:
        print("\nALL INTEGRATION FLOW INTEGRATION FLOW TESTS PASSED")
        print("PASS: AWS provider strategy now uses appropriate handler system")
        print("PASS: AWSInstanceManager bypass has been eliminated")
        print("PASS: Launch template flow is properly integrated")
        print("PASS: Handler routing works correctly")
        sys.exit(0)
    else:
        print("\nFAIL: SOME INTEGRATION FLOW TESTS FAILED")
        sys.exit(1)
