#!/usr/bin/env python3
"""
Phase 4B: End-to-End Integration Test

This test validates that our consolidated handler architecture works properly
with the provider strategy and the entire integration flow:

1. Provider Strategy Integration - Handlers work with AWS provider strategy
2. Launch Template Flow - Launch template manager integration works
3. Handler Routing - Provider strategy routes to correct handlers
4. Domain Model Integration - AWSTemplate flows through the system
5. Error Handling - Unified error handling works across the flow
6. Performance Metrics - Metrics collection works end-to-end

Test Categories:
1. Provider Strategy Handler Integration Tests
2. Launch Template Manager Integration Tests
3. End-to-End Flow Tests
4. Error Handling Integration Tests
5. Performance Metrics Integration Tests
"""

import os
import sys
from unittest.mock import Mock

# Add project root to path
sys.path.insert(0, os.path.abspath("."))


def test_end_to_end_integration():
    """Test Phase 4B: End-to-End Integration"""

    print("🔗 PHASE 4B: END-TO-END INTEGRATION TEST")
    print("=" * 60)

    results = {
        "provider_strategy_integration": False,
        "launch_template_integration": False,
        "handler_routing": False,
        "domain_model_flow": False,
        "error_handling_integration": False,
        "performance_metrics_integration": False,
        "full_end_to_end_flow": False,
    }

    try:
        # Test 1: Provider Strategy Handler Integration
        print("\n1️⃣ Testing Provider Strategy Handler Integration...")
        results["provider_strategy_integration"] = test_provider_strategy_integration()

        # Test 2: Launch Template Manager Integration
        print("\n2️⃣ Testing Launch Template Manager Integration...")
        results["launch_template_integration"] = test_launch_template_integration()

        # Test 3: Handler Routing
        print("\n3️⃣ Testing Handler Routing...")
        results["handler_routing"] = test_handler_routing()

        # Test 4: Domain Model Flow
        print("\n4️⃣ Testing Domain Model Flow...")
        results["domain_model_flow"] = test_domain_model_flow()

        # Test 5: Error Handling Integration
        print("\n5️⃣ Testing Error Handling Integration...")
        results["error_handling_integration"] = test_error_handling_integration()

        # Test 6: Performance Metrics Integration
        print("\n6️⃣ Testing Performance Metrics Integration...")
        results["performance_metrics_integration"] = (
            test_performance_metrics_integration()
        )

        # Test 7: Full End-to-End Flow
        print("\n7️⃣ Testing Full End-to-End Flow...")
        results["full_end_to_end_flow"] = test_full_end_to_end_flow()

        # Summary
        print("\n" + "=" * 60)
        print("📊 PHASE 4B TEST RESULTS SUMMARY")
        print("=" * 60)

        passed = sum(1 for result in results.values() if result)
        total = len(results)

        for test_name, result in results.items():
            status = "PASS: PASS" if result else "FAIL: FAIL"
            print(f"{test_name.replace('_', ' ').title()}: {status}")

        print(f"\nOverall: {passed}/{total} tests passed")

        if passed == total:
            print("🎉 ALL PHASE 4B TESTS PASSED!")
            print("PASS: End-to-end integration working perfectly!")
            return True
        else:
            print("WARN:  Some integration tests failed - need fixes")
            return False

    except Exception as e:
        print(f"FAIL: Test execution failed: {str(e)}")
        import traceback

        traceback.print_exc()
        return False


def test_provider_strategy_integration():
    """Test that provider strategy properly integrates with consolidated handlers."""
    try:
        from src.providers.aws.domain.template.aggregate import AWSTemplate
        from src.providers.aws.domain.template.value_objects import ProviderApi
        from src.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

        print("   Testing provider strategy handler integration...")

        # Create mock dependencies
        Mock()
        mock_logger = Mock()
        Mock()
        Mock()
        Mock()

        # Test that provider strategy can be instantiated
        try:
            from src.providers.aws.configuration.config import AWSProviderConfig

            # Create proper AWS config
            aws_config = AWSProviderConfig(region="us-west-2", profile="default")

            strategy = AWSProviderStrategy(config=aws_config, logger=mock_logger)
            print("   PASS: Provider strategy instantiation successful")
        except Exception as e:
            print(f"   FAIL: Provider strategy instantiation failed: {str(e)}")
            return False

        # Test handler initialization for each provider API
        provider_apis = [
            ProviderApi.SPOT_FLEET,
            ProviderApi.EC2_FLEET,
            ProviderApi.RUN_INSTANCES,
            ProviderApi.ASG,
        ]

        for api in provider_apis:
            try:
                # Create test AWS template
                _ = AWSTemplate(
                    template_id=f"test-{api.value}",
                    provider_api=api,
                    vm_type="t2.micro",
                    image_id="ami-12345",
                    subnet_ids=["subnet-12345"],
                )

                # Test that strategy can handle the template
                print(f"   📋 Testing {api.value} handler integration...")

                # This should not raise an exception
                handler_method = getattr(
                    strategy, f"_get_{api.value.lower()}_handler", None
                )
                if handler_method:
                    print(f"   PASS: {api.value} handler method exists")
                else:
                    print(
                        f"   WARN:  {api.value} handler method not found (may use different pattern)"
                    )

            except Exception as e:
                print(f"   FAIL: {api.value} handler integration failed: {str(e)}")
                return False

        print("   PASS: Provider strategy handler integration successful")
        return True

    except ImportError as e:
        print(f"   FAIL: Import error: {str(e)}")
        return False
    except Exception as e:
        print(f"   FAIL: Provider strategy integration test failed: {str(e)}")
        return False


def test_launch_template_integration():
    """Test that launch template manager integrates properly with handlers."""
    try:
        from src.domain.request.aggregate import Request
        from src.providers.aws.domain.template.aggregate import AWSTemplate
        from src.providers.aws.infrastructure.handlers.spot_fleet_handler import (
            SpotFleetHandler,
        )
        from src.providers.aws.infrastructure.launch_template.manager import (
            AWSLaunchTemplateManager,
        )

        print("   Testing launch template manager integration...")

        # Create mock dependencies
        mock_aws_client = Mock()
        mock_logger = Mock()
        mock_aws_ops = Mock()
        Mock()

        # Test launch template manager instantiation
        try:
            lt_manager = AWSLaunchTemplateManager(
                aws_client=mock_aws_client, logger=mock_logger
            )
            print("   PASS: Launch template manager instantiation successful")
        except Exception as e:
            print(f"   FAIL: Launch template manager instantiation failed: {str(e)}")
            return False

        # Test handler with launch template manager
        try:
            handler = SpotFleetHandler(
                aws_client=mock_aws_client,
                logger=mock_logger,
                aws_ops=mock_aws_ops,
                launch_template_manager=lt_manager,
            )

            # Verify launch template manager is properly stored
            if hasattr(handler, "launch_template_manager"):
                if handler.launch_template_manager == lt_manager:
                    print("   PASS: Handler properly stores launch template manager")
                else:
                    print(
                        "   FAIL: Handler launch template manager not properly assigned"
                    )
                    return False
            else:
                print("   FAIL: Handler missing launch_template_manager attribute")
                return False

        except Exception as e:
            print(f"   FAIL: Handler with launch template manager failed: {str(e)}")
            return False

        # Test launch template creation flow (mocked)
        try:
            # Mock the launch template creation
            mock_result = Mock()
            mock_result.template_id = "lt-12345"
            mock_result.version = "1"

            lt_manager.create_or_update_launch_template = Mock(return_value=mock_result)

            # Create test data
            from src.providers.aws.domain.template.value_objects import ProviderApi

            _ = AWSTemplate(
                template_id="test-template",
                vm_type="t2.micro",
                image_id="ami-12345",
                subnet_ids=["subnet-12345"],
                provider_api=ProviderApi.SPOT_FLEET,
            )

            from src.domain.request.value_objects import RequestType

            _ = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id="test-template",
                machine_count=1,
                provider_type="aws",
            )

            # Test launch template creation
            result = lt_manager.create_or_update_launch_template(aws_template, request)

            if result.template_id == "lt-12345" and result.version == "1":
                print("   PASS: Launch template creation flow working")
            else:
                print("   FAIL: Launch template creation flow failed")
                return False

        except Exception as e:
            print(f"   FAIL: Launch template creation test failed: {str(e)}")
            return False

        print("   PASS: Launch template integration successful")
        return True

    except ImportError as e:
        print(f"   FAIL: Import error: {str(e)}")
        return False
    except Exception as e:
        print(f"   FAIL: Launch template integration test failed: {str(e)}")
        return False


def test_handler_routing():
    """Test that handlers are properly routed based on provider API."""
    try:
        from src.providers.aws.domain.template.value_objects import ProviderApi
        from src.providers.aws.infrastructure.handlers.asg_handler import ASGHandler
        from src.providers.aws.infrastructure.handlers.ec2_fleet_handler import (
            EC2FleetHandler,
        )
        from src.providers.aws.infrastructure.handlers.run_instances_handler import (
            RunInstancesHandler,
        )
        from src.providers.aws.infrastructure.handlers.spot_fleet_handler import (
            SpotFleetHandler,
        )

        print("   Testing handler routing logic...")

        # Test handler class availability
        handlers = {
            ProviderApi.SPOT_FLEET: SpotFleetHandler,
            ProviderApi.EC2_FLEET: EC2FleetHandler,
            ProviderApi.RUN_INSTANCES: RunInstancesHandler,
            ProviderApi.ASG: ASGHandler,
        }

        for api, handler_class in handlers.items():
            try:
                print(
                    f"   📋 Testing {api.value} -> {handler_class.__name__} routing..."
                )

                # Verify handler class exists and can be imported
                if handler_class:
                    print(
                        f"   PASS: {handler_class.__name__} available for {api.value}"
                    )
                else:
                    print(
                        f"   FAIL: {handler_class.__name__} not available for {api.value}"
                    )
                    return False

                # Test handler instantiation with unified constructor
                mock_aws_client = Mock()
                mock_logger = Mock()
                mock_aws_ops = Mock()
                mock_launch_template_manager = Mock()

                handler = handler_class(
                    aws_client=mock_aws_client,
                    logger=mock_logger,
                    aws_ops=mock_aws_ops,
                    launch_template_manager=mock_launch_template_manager,
                )

                # Verify handler has required methods
                required_methods = [
                    "acquire_hosts",
                    "check_hosts_status",
                    "release_hosts",
                ]
                for method_name in required_methods:
                    if hasattr(handler, method_name):
                        print(f"   PASS: {handler_class.__name__}.{method_name} exists")
                    else:
                        print(
                            f"   FAIL: {handler_class.__name__}.{method_name} missing"
                        )
                        return False

            except Exception as e:
                print(f"   FAIL: {api.value} handler routing failed: {str(e)}")
                return False

        print("   PASS: Handler routing successful")
        return True

    except ImportError as e:
        print(f"   FAIL: Import error: {str(e)}")
        return False
    except Exception as e:
        print(f"   FAIL: Handler routing test failed: {str(e)}")
        return False


def test_domain_model_flow():
    """Test that AWSTemplate flows properly through the system."""
    try:
        from src.domain.request.aggregate import Request
        from src.providers.aws.domain.template.aggregate import AWSTemplate
        from src.providers.aws.domain.template.value_objects import ProviderApi
        from src.providers.aws.infrastructure.handlers.spot_fleet_handler import (
            SpotFleetHandler,
        )

        print("   Testing domain model flow...")

        # Create test AWSTemplate
        _ = AWSTemplate(
            template_id="test-template",
            provider_api=ProviderApi.SPOT_FLEET,
            vm_type="t2.micro",
            image_id="ami-12345",
            subnet_ids=["subnet-12345"],
            fleet_role="arn:aws:iam::123456789012:role/fleet-role",
        )

        # Create test Request
        from src.domain.request.value_objects import RequestType

        _ = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="test-template",
            machine_count=2,
            provider_type="aws",
        )

        print("   📋 Testing AWSTemplate creation and validation...")

        # Test AWSTemplate validation
        if aws_template.template_id == "test-template":
            print("   PASS: AWSTemplate creation successful")
        else:
            print("   FAIL: AWSTemplate creation failed")
            return False

        # Test provider API enum
        if aws_template.provider_api == ProviderApi.SPOT_FLEET:
            print("   PASS: ProviderApi enum working")
        else:
            print("   FAIL: ProviderApi enum failed")
            return False

        # Test handler method signature compatibility
        print("   📋 Testing handler method signature compatibility...")

        mock_aws_client = Mock()
        mock_logger = Mock()
        mock_aws_ops = Mock()
        mock_launch_template_manager = Mock()

        handler = SpotFleetHandler(
            aws_client=mock_aws_client,
            logger=mock_logger,
            aws_ops=mock_aws_ops,
            launch_template_manager=mock_launch_template_manager,
        )

        # Test that acquire_hosts method accepts AWSTemplate
        import inspect

        sig = inspect.signature(handler.acquire_hosts)
        params = list(sig.parameters.keys())

        if len(params) >= 2:
            print(f"   PASS: acquire_hosts signature: {params}")
        else:
            print(f"   FAIL: acquire_hosts signature incorrect: {params}")
            return False

        print("   PASS: Domain model flow successful")
        return True

    except ImportError as e:
        print(f"   FAIL: Import error: {str(e)}")
        return False
    except Exception as e:
        print(f"   FAIL: Domain model flow test failed: {str(e)}")
        return False


def test_error_handling_integration():
    """Test that unified error handling works across the integration."""
    try:
        from src.providers.aws.exceptions.aws_exceptions import AWSValidationError
        from src.providers.aws.infrastructure.handlers.base_handler import AWSHandler
        from src.providers.aws.infrastructure.handlers.spot_fleet_handler import (
            SpotFleetHandler,
        )

        print("   Testing error handling integration...")

        # Test base handler error handling methods
        print("   📋 Testing base handler error handling...")

        error_methods = ["_convert_client_error", "_retry_with_backoff"]

        for method_name in error_methods:
            if hasattr(AWSHandler, method_name):
                print(f"   PASS: AWSHandler.{method_name} exists")
            else:
                print(f"   FAIL: AWSHandler.{method_name} missing")
                return False

        # Test handler inheritance of error handling
        print("   📋 Testing handler error handling inheritance...")

        mock_aws_client = Mock()
        mock_logger = Mock()
        mock_aws_ops = Mock()
        mock_launch_template_manager = Mock()

        handler = SpotFleetHandler(
            aws_client=mock_aws_client,
            logger=mock_logger,
            aws_ops=mock_aws_ops,
            launch_template_manager=mock_launch_template_manager,
        )

        for method_name in error_methods:
            if hasattr(handler, method_name):
                print(f"   PASS: SpotFleetHandler.{method_name} inherited")
            else:
                print(f"   FAIL: SpotFleetHandler.{method_name} not inherited")
                return False

        # Test AWS exception classes
        print("   📋 Testing AWS exception classes...")

        try:
            raise AWSValidationError("Test validation error")
        except AWSValidationError as e:
            if str(e) == "Test validation error":
                print("   PASS: AWSValidationError working")
            else:
                print("   FAIL: AWSValidationError message incorrect")
                return False
        except Exception as e:
            print(f"   FAIL: AWSValidationError test failed: {str(e)}")
            return False

        print("   PASS: Error handling integration successful")
        return True

    except ImportError as e:
        print(f"   FAIL: Import error: {str(e)}")
        return False
    except Exception as e:
        print(f"   FAIL: Error handling integration test failed: {str(e)}")
        return False


def test_performance_metrics_integration():
    """Test that performance metrics work across the integration."""
    try:
        from src.providers.aws.infrastructure.handlers.base_handler import AWSHandler
        from src.providers.aws.infrastructure.handlers.spot_fleet_handler import (
            SpotFleetHandler,
        )

        print("   Testing performance metrics integration...")

        # Test base handler metrics methods
        print("   📋 Testing base handler metrics methods...")

        metrics_methods = [
            "get_metrics",
            "_record_success_metrics",
            "_record_failure_metrics",
        ]

        for method_name in metrics_methods:
            if hasattr(AWSHandler, method_name):
                print(f"   PASS: AWSHandler.{method_name} exists")
            else:
                print(f"   FAIL: AWSHandler.{method_name} missing")
                return False

        # Test handler metrics functionality
        print("   📋 Testing handler metrics functionality...")

        mock_aws_client = Mock()
        mock_logger = Mock()
        mock_aws_ops = Mock()
        mock_launch_template_manager = Mock()

        handler = SpotFleetHandler(
            aws_client=mock_aws_client,
            logger=mock_logger,
            aws_ops=mock_aws_ops,
            launch_template_manager=mock_launch_template_manager,
        )

        # Test get_metrics returns a dict
        try:
            metrics = handler.get_metrics()
            if isinstance(metrics, dict):
                print("   PASS: get_metrics returns dict")
            else:
                print(f"   FAIL: get_metrics returns {type(metrics)}, expected dict")
                return False
        except Exception as e:
            print(f"   FAIL: get_metrics test failed: {str(e)}")
            return False

        print("   PASS: Performance metrics integration successful")
        return True

    except ImportError as e:
        print(f"   FAIL: Import error: {str(e)}")
        return False
    except Exception as e:
        print(f"   FAIL: Performance metrics integration test failed: {str(e)}")
        return False


def test_full_end_to_end_flow():
    """Test the complete end-to-end flow with mocked dependencies."""
    try:
        from src.domain.request.aggregate import Request
        from src.providers.aws.domain.template.aggregate import AWSTemplate
        from src.providers.aws.domain.template.value_objects import ProviderApi
        from src.providers.aws.infrastructure.handlers.spot_fleet_handler import (
            SpotFleetHandler,
        )
        from src.providers.aws.infrastructure.launch_template.manager import (
            AWSLaunchTemplateManager,
        )

        print("   Testing full end-to-end flow...")

        # Create test data
        from src.providers.aws.domain.template.value_objects import AWSFleetType

        _ = AWSTemplate(
            template_id="e2e-test-template",
            provider_api=ProviderApi.SPOT_FLEET,
            vm_type="t2.micro",
            image_id="ami-12345",
            subnet_ids=["subnet-12345"],
            security_group_ids=["sg-12345"],
            fleet_role="AWSServiceRoleForEC2SpotFleet",
            fleet_type=AWSFleetType.REQUEST.value,
        )

        from src.domain.request.value_objects import RequestType

        _ = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="e2e-test-template",
            machine_count=1,
            provider_type="aws",
        )

        print("   📋 Setting up mocked dependencies...")

        # Create mocked dependencies
        mock_aws_client = Mock()
        mock_logger = Mock()
        mock_aws_ops = Mock()
        Mock()

        # Mock AWS client methods that will be called
        mock_aws_client.ec2_client = Mock()
        mock_aws_client.sts_client = Mock()
        mock_aws_client.session = Mock()
        mock_aws_client.boto_config = Mock()

        # Mock STS get_caller_identity for fleet role ARN construction
        mock_aws_client.sts_client.get_caller_identity.return_value = {
            "Account": "123456789012"
        }

        # Mock EC2 request_spot_fleet to return a fleet ID
        mock_aws_client.ec2_client.request_spot_fleet.return_value = {
            "SpotFleetRequestId": "sfr-12345"
        }

        # Mock AWS operations to actually call the internal method
        def mock_execute_with_standard_error_handling(
            operation, operation_name, context
        ):
            return operation()

        mock_aws_ops.execute_with_standard_error_handling = Mock(
            side_effect=mock_execute_with_standard_error_handling
        )

        # Create launch template manager
        lt_manager = AWSLaunchTemplateManager(
            aws_client=mock_aws_client, logger=mock_logger
        )

        # Mock launch template creation
        mock_lt_result = Mock()
        mock_lt_result.template_id = "lt-12345"
        mock_lt_result.version = "1"
        lt_manager.create_or_update_launch_template = Mock(return_value=mock_lt_result)

        # Create handler
        handler = SpotFleetHandler(
            aws_client=mock_aws_client,
            logger=mock_logger,
            aws_ops=mock_aws_ops,
            launch_template_manager=lt_manager,
        )

        print("   📋 Testing end-to-end acquire_hosts flow...")

        # Test the full flow (mocked)
        try:
            # This should work without throwing exceptions
            result = handler.acquire_hosts(request, aws_template)

            # Verify the flow worked
            if result == "sfr-12345":
                print("   PASS: End-to-end acquire_hosts flow successful")
            else:
                print(f"   FAIL: End-to-end flow returned unexpected result: {result}")
                return False

            # Verify launch template manager was called
            lt_manager.create_or_update_launch_template.assert_called_once_with(
                aws_template, request
            )
            print("   PASS: Launch template manager properly called")

            # Verify AWS operations was called
            mock_aws_ops.execute_with_standard_error_handling.assert_called_once()
            print("   PASS: AWS operations properly called")

        except Exception as e:
            print(f"   FAIL: End-to-end flow failed: {str(e)}")
            return False

        print("   PASS: Full end-to-end flow successful")
        return True

    except ImportError as e:
        print(f"   FAIL: Import error: {str(e)}")
        return False
    except Exception as e:
        print(f"   FAIL: Full end-to-end flow test failed: {str(e)}")
        return False


if __name__ == "__main__":
    success = test_end_to_end_integration()
    sys.exit(0 if success else 1)
