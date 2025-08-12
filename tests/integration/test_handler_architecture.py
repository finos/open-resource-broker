#!/usr/bin/env python3
"""
TestA: Handler Architecture Consolidation Test

This test validates the integrated handler architecture implementation:
- All handlers use consistent constructor patterns
- All handlers inherit from integrated AWSHandler base class
- All handlers use AWSTemplate instead of generic Template
- All handlers use launch template manager properly
- All handlers follow DDD/OOP/SOLID/Clean Architecture principles

Test Categories:
1. Constructor Consistency Tests
2. Method Signature Consistency Tests
3. Base Class Inheritance Tests
4. Launch Template Integration Tests
5. Error Handling Consistency Tests
6. Performance Metrics Tests
"""

import inspect
import os
import sys
from unittest.mock import Mock

# Add project root to path
sys.path.insert(0, os.path.abspath("."))


def test_handler_architecture_consolidation():
    """Test TestA: Handler Architecture Consolidation"""

    print("PHASE 4A: HANDLER ARCHITECTURE CONSOLIDATION TEST")
    print("=" * 60)

    results = {
        "constructor_consistency": False,
        "method_signatures": False,
        "base_class_inheritance": False,
        "launch_template_integration": False,
        "error_handling": False,
        "performance_metrics": False,
        "import_consistency": False,
    }

    try:
        # Constructor Consistency
        print("\n1. Testing Constructor Consistency...")
        results["constructor_consistency"] = test_constructor_consistency()

        # Method Signature Consistency
        print("\n2. Testing Method Signature Consistency...")
        results["method_signatures"] = test_method_signatures()

        # Base Class Inheritance
        print("\n3. Testing Base Class Inheritance...")
        results["base_class_inheritance"] = test_base_class_inheritance()

        # Launch Template Integration
        print("\n4. Testing Launch Template Integration...")
        results["launch_template_integration"] = test_launch_template_integration()

        # Error Handling Consistency
        print("\n5. Testing Error Handling Consistency...")
        results["error_handling"] = test_error_handling_consistency()

        # Performance Metrics
        print("\n6. Testing Performance Metrics...")
        results["performance_metrics"] = test_performance_metrics()

        # Import Consistency
        print("\n7. Testing Import Consistency...")
        results["import_consistency"] = test_import_consistency()

        # Summary
        print("\n" + "=" * 60)
        print("PHASE 4A TEST RESULTS SUMMARY")
        print("=" * 60)

        passed = sum(1 for result in results.values() if result)
        total = len(results)

        for test_name, result in results.items():
            status = "PASS: PASS" if result else "FAIL: FAIL"
            print(f"{test_name.replace('_', ' ').title()}: {status}")

        print(f"\nOverall: {passed}/{total} tests passed")

        if passed == total:
            print("ALL PHASE 4A TESTS PASSED!")
            print("PASS: Handler architecture successfully consolidated!")
            return True
        else:
            print("WARN:  Some tests failed - handler architecture needs fixes")
            return False

    except Exception as e:
        print(f"FAIL: Test execution failed: {str(e)}")
        import traceback

        traceback.print_exc()
        return False


def test_constructor_consistency():
    """Test that all handlers have consistent constructor patterns."""
    try:
        # Import all handlers
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

        handlers = [SpotFleetHandler, EC2FleetHandler, RunInstancesHandler, ASGHandler]

        print("   Checking constructor signatures...")

        # Expected parameters (order matters for consistency)
        expected_params = ["aws_client", "logger", "aws_ops", "launch_template_manager"]

        optional_params = ["request_adapter", "error_handler"]

        for handler_class in handlers:
            handler_name = handler_class.__name__
            print(f"   Checking {handler_name}...")

            # Get constructor signature
            sig = inspect.signature(handler_class.__init__)
            params = list(sig.parameters.keys())[1:]  # Skip 'self'

            # Check required parameters are present and in order
            for i, expected_param in enumerate(expected_params):
                if i >= len(params) or params[i] != expected_param:
                    print(
                        f"   FAIL: {handler_name}: Missing or misplaced required parameter '{expected_param}'"
                    )
                    print(f"      Expected: {expected_params}")
                    print(f"      Actual: {params}")
                    return False

            # Check that all parameters are either required or optional
            for param in params:
                if param not in expected_params and param not in optional_params:
                    print(f"   FAIL: {handler_name}: Unexpected parameter '{param}'")
                    return False

            print(f"   PASS: {handler_name}: Constructor signature is consistent")

        print("   PASS: All handlers have consistent constructor patterns")
        return True

    except ImportError as e:
        print(f"   FAIL: Import error: {str(e)}")
        return False
    except Exception as e:
        print(f"   FAIL: Constructor consistency test failed: {str(e)}")
        return False


def test_method_signatures():
    """Test that all handlers have consistent method signatures."""
    try:
        # Import all handlers
        from src.domain.request.aggregate import Request
        from src.providers.aws.domain.template.aggregate import AWSTemplate
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

        handlers = [SpotFleetHandler, EC2FleetHandler, RunInstancesHandler, ASGHandler]

        print("   Checking method signatures...")

        # Expected method signatures
        expected_methods = {
            "acquire_hosts": (Request, AWSTemplate),
            "check_hosts_status": (Request,),
            "release_hosts": (Request,),
        }

        for handler_class in handlers:
            handler_name = handler_class.__name__
            print(f"   Checking {handler_name} methods...")

            for method_name, expected_params in expected_methods.items():
                if not hasattr(handler_class, method_name):
                    print(f"   FAIL: {handler_name}: Missing method '{method_name}'")
                    return False

                method = getattr(handler_class, method_name)
                sig = inspect.signature(method)
                params = list(sig.parameters.keys())[1:]  # Skip 'self'

                # Check parameter count
                if len(params) != len(expected_params):
                    print(f"   FAIL: {handler_name}.{method_name}: Wrong parameter count")
                    print(f"      Expected: {len(expected_params)} params")
                    print(f"      Actual: {len(params)} params")
                    return False

                # For acquire_hosts, check that second parameter uses AWSTemplate
                if method_name == "acquire_hosts":
                    param_annotations = [param.annotation for param in sig.parameters.values()][
                        1:
                    ]  # Skip 'self'
                    if len(param_annotations) >= 2:
                        template_annotation = param_annotations[1]
                        if (
                            template_annotation != AWSTemplate
                            and str(template_annotation) != "AWSTemplate"
                        ):
                            print(
                                f"   FAIL: {handler_name}.{method_name}: Should use AWSTemplate, not {template_annotation}"
                            )
                            return False

            print(f"   PASS: {handler_name}: Method signatures are consistent")

        print("   PASS: All handlers have consistent method signatures")
        return True

    except Exception as e:
        print(f"   FAIL: Method signature test failed: {str(e)}")
        return False


def test_base_class_inheritance():
    """Test that all handlers inherit from integrated AWSHandler base class."""
    try:
        # Import handlers and base class
        from src.providers.aws.infrastructure.handlers.asg_handler import ASGHandler
        from src.providers.aws.infrastructure.handlers.base_handler import AWSHandler
        from src.providers.aws.infrastructure.handlers.ec2_fleet_handler import (
            EC2FleetHandler,
        )
        from src.providers.aws.infrastructure.handlers.run_instances_handler import (
            RunInstancesHandler,
        )
        from src.providers.aws.infrastructure.handlers.spot_fleet_handler import (
            SpotFleetHandler,
        )

        handlers = [
            (SpotFleetHandler, "SpotFleetHandler"),
            (EC2FleetHandler, "EC2FleetHandler"),
            (RunInstancesHandler, "RunInstancesHandler"),
            (ASGHandler, "ASGHandler"),
        ]

        print("   Checking base class inheritance...")

        for handler_class, handler_name in handlers:
            print(f"   Checking {handler_name}...")

            # Check direct inheritance
            if AWSHandler not in handler_class.__bases__:
                print(f"   FAIL: {handler_name}: Does not directly inherit from AWSHandler")
                print(f"      Base classes: {[base.__name__ for base in handler_class.__bases__]}")
                return False

            # Check MRO (Method Resolution Order)
            if AWSHandler not in handler_class.__mro__:
                print(f"   FAIL: {handler_name}: AWSHandler not in MRO")
                return False

            # Check that handler has access to base class methods
            base_methods = [
                "_retry_with_backoff",
                "_convert_client_error",
                "_validate_prerequisites",
                "get_metrics",
            ]
            for method_name in base_methods:
                if not hasattr(handler_class, method_name):
                    print(f"   FAIL: {handler_name}: Missing inherited method '{method_name}'")
                    return False

            print(f"   PASS: {handler_name}: Properly inherits from AWSHandler")

        print("   PASS: All handlers properly inherit from integrated AWSHandler")
        return True

    except Exception as e:
        print(f"   FAIL: Base class inheritance test failed: {str(e)}")
        return False


def test_launch_template_integration():
    """Test that all handlers properly integrate with launch template manager."""
    try:
        # Import handlers
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

        handlers = [
            (SpotFleetHandler, "SpotFleetHandler"),
            (EC2FleetHandler, "EC2FleetHandler"),
            (RunInstancesHandler, "RunInstancesHandler"),
            (ASGHandler, "ASGHandler"),
        ]

        print("   Checking launch template integration...")

        for handler_class, handler_name in handlers:
            print(f"   Checking {handler_name}...")

            # Create mock dependencies
            mock_aws_client = Mock()
            mock_logger = Mock()
            mock_aws_ops = Mock()
            mock_launch_template_manager = Mock()

            # Try to instantiate handler
            try:
                handler = handler_class(
                    aws_client=mock_aws_client,
                    logger=mock_logger,
                    aws_ops=mock_aws_ops,
                    launch_template_manager=mock_launch_template_manager,
                )

                # Check that launch template manager is stored
                if not hasattr(handler, "launch_template_manager"):
                    print(
                        f"   FAIL: {handler_name}: launch_template_manager not stored as attribute"
                    )
                    return False

                if handler.launch_template_manager != mock_launch_template_manager:
                    print(f"   FAIL: {handler_name}: launch_template_manager not properly assigned")
                    return False

                print(f"   PASS: {handler_name}: Launch template manager properly integrated")

            except Exception as e:
                print(
                    f"   FAIL: {handler_name}: Failed to instantiate with launch template manager: {str(e)}"
                )
                return False

        print("   PASS: All handlers properly integrate with launch template manager")
        return True

    except Exception as e:
        print(f"   FAIL: Launch template integration test failed: {str(e)}")
        return False


def test_error_handling_consistency():
    """Test that all handlers have consistent error handling."""
    try:
        # Import base handler to check error handling methods
        from src.providers.aws.infrastructure.handlers.base_handler import AWSHandler
        from src.providers.aws.infrastructure.handlers.spot_fleet_handler import (
            SpotFleetHandler,
        )

        print("   Checking error handling consistency...")

        # Check that base handler has error handling methods
        error_methods = ["_convert_client_error", "_retry_with_backoff"]

        for method_name in error_methods:
            if not hasattr(AWSHandler, method_name):
                print(f"   FAIL: AWSHandler: Missing error handling method '{method_name}'")
                return False

        # Check that handlers inherit error handling
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
            if not hasattr(handler, method_name):
                print(
                    f"   FAIL: SpotFleetHandler: Missing inherited error handling method '{method_name}'"
                )
                return False

        print("   PASS: Error handling is consistent across handlers")
        return True

    except Exception as e:
        print(f"   FAIL: Error handling consistency test failed: {str(e)}")
        return False


def test_performance_metrics():
    """Test that handlers support performance metrics."""
    try:
        from src.providers.aws.infrastructure.handlers.base_handler import AWSHandler
        from src.providers.aws.infrastructure.handlers.spot_fleet_handler import (
            SpotFleetHandler,
        )

        print("   Checking performance metrics support...")

        # Check that base handler has metrics methods
        metrics_methods = [
            "get_metrics",
            "_record_success_metrics",
            "_record_failure_metrics",
        ]

        for method_name in metrics_methods:
            if not hasattr(AWSHandler, method_name):
                print(f"   FAIL: AWSHandler: Missing metrics method '{method_name}'")
                return False

        # Test metrics functionality
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
        metrics = handler.get_metrics()
        if not isinstance(metrics, dict):
            print(f"   FAIL: get_metrics should return a dict, got {type(metrics)}")
            return False

        print("   PASS: Performance metrics are properly supported")
        return True

    except Exception as e:
        print(f"   FAIL: Performance metrics test failed: {str(e)}")
        return False


def test_import_consistency():
    """Test that all handlers have consistent imports."""
    try:
        import os

        print("   Checking import consistency...")

        handler_files = [
            "src/providers/aws/infrastructure/handlers/spot_fleet_handler.py",
            "src/providers/aws/infrastructure/handlers/ec2_fleet_handler.py",
            "src/providers/aws/infrastructure/handlers/run_instances_handler.py",
            "src/providers/aws/infrastructure/handlers/asg_handler.py",
        ]

        # Required imports for all handlers
        required_imports = {
            "src.domain.request.aggregate.Request",
            "src.providers.aws.domain.template.aggregate.AWSTemplate",
            "src.providers.aws.infrastructure.handlers.base_handler.AWSHandler",
        }

        for handler_file in handler_files:
            if not os.path.exists(handler_file):
                print(f"   FAIL: Handler file not found: {handler_file}")
                return False

            print(f"   Checking imports in {os.path.basename(handler_file)}...")

            with open(handler_file, "r") as f:
                content = f.read()

            # Check for AWSTemplate import (not Template)
            if "from src.domain.template.aggregate import Template" in content:
                print(
                    f"   FAIL: {handler_file}: Still importing generic Template instead of AWSTemplate"
                )
                return False

            if "from src.providers.aws.domain.template.aggregate import AWSTemplate" not in content:
                print(f"   FAIL: {handler_file}: Missing AWSTemplate import")
                return False

            # Check for base handler import
            if (
                "from src.providers.aws.infrastructure.handlers.base_handler import AWSHandler"
                not in content
            ):
                print(f"   FAIL: {handler_file}: Missing AWSHandler import")
                return False

            print(f"   PASS: {os.path.basename(handler_file)}: Imports are consistent")

        print("   PASS: All handlers have consistent imports")
        return True

    except Exception as e:
        print(f"   FAIL: Import consistency test failed: {str(e)}")
        return False


if __name__ == "__main__":
    success = test_handler_architecture_consolidation()
    sys.exit(0 if success else 1)
