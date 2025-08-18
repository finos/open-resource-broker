#!/usr/bin/env python3
"""
Isolated Domain Validation Test

This test isolates domain model validation issues to understand what's failing
and ensure we're creating valid domain objects correctly.
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath("."))


def test_domain_validation():
    """Test domain model validation in isolation."""

    print("ISOLATED DOMAIN VALIDATION TEST")
    print("=" * 50)

    results = {
        "aws_template_basic": False,
        "aws_template_spot_fleet": False,
        "request_creation": False,
        "base_handler_validation": False,
    }

    try:
        # Basic AWSTemplate creation
        print("\n1. Testing Basic AWSTemplate Creation...")
        results["aws_template_basic"] = test_aws_template_basic()

        # SpotFleet AWSTemplate creation
        print("\n2. Testing SpotFleet AWSTemplate Creation...")
        results["aws_template_spot_fleet"] = test_aws_template_spot_fleet()

        # Request creation
        print("\n3. Testing Request Creation...")
        results["request_creation"] = test_request_creation()

        # Base handler validation
        print("\n4. Testing Base Handler Validation...")
        results["base_handler_validation"] = test_base_handler_validation()

        # Summary
        print("\n" + "=" * 50)
        print("DOMAIN VALIDATION TEST RESULTS")
        print("=" * 50)

        passed = sum(1 for result in results.values() if result)
        total = len(results)

        for test_name, result in results.items():
            status = "PASS: PASS" if result else "FAIL: FAIL"
            print(f"{test_name.replace('_', ' ').title()}: {status}")

        print(f"\nOverall: {passed}/{total} tests passed")

        if passed == total:
            print("ALL DOMAIN VALIDATION TESTS PASSED!")
            return True
        else:
            print("WARN: Some domain validation tests failed")
            return False

    except Exception as e:
        print(f"FAIL: Test execution failed: {str(e)}")
        import traceback

        traceback.print_exc()
        return False


def test_aws_template_basic():
    """Test basic AWSTemplate creation."""
    try:
        from providers.aws.domain.template.aggregate import AWSTemplate
        from providers.aws.domain.template.value_objects import ProviderApi

        print("   Testing basic AWSTemplate creation...")

        # Test minimal template
        try:
            template = AWSTemplate(
                template_id="test-basic",
                provider_api=ProviderApi.RUN_INSTANCES,
                instance_type="t2.micro",
                image_id="ami-12345",
                subnet_ids=["subnet-12345"],
                security_group_ids=["sg-12345"],
            )

            print(f"   PASS: Basic template created: {template.template_id}")
            print(f"   Provider API: {template.provider_api}")
            print(f"   Instance Type: {template.instance_type}")
            print(f"   Image ID: {template.image_id}")
            print(f"   Subnet IDs: {template.subnet_ids}")
            print(f"   Security Group IDs: {template.security_group_ids}")

            return True

        except Exception as e:
            print(f"   FAIL: Basic template creation failed: {str(e)}")
            import traceback

            traceback.print_exc()
            return False

    except ImportError as e:
        print(f"   FAIL: Import error: {str(e)}")
        return False


def test_aws_template_spot_fleet():
    """Test SpotFleet AWSTemplate creation."""
    try:
        from providers.aws.domain.template.aggregate import AWSTemplate
        from providers.aws.domain.template.value_objects import (
            AWSFleetType,
            ProviderApi,
        )

        print("   Testing SpotFleet AWSTemplate creation...")

        # Test SpotFleet template
        try:
            template = AWSTemplate(
                template_id="test-spot-fleet",
                provider_api=ProviderApi.SPOT_FLEET,
                instance_type="t2.micro",
                image_id="ami-12345",
                subnet_ids=["subnet-12345"],
                security_group_ids=["sg-12345"],
                fleet_role="AWSServiceRoleForEC2SpotFleet",
                fleet_type=AWSFleetType.REQUEST,
            )

            print(f"   PASS: SpotFleet template created: {template.template_id}")
            print(f"   Provider API: {template.provider_api}")
            print(f"   Fleet Type: {template.fleet_type}")
            print(f"   Fleet Role: {template.fleet_role}")

            return True

        except Exception as e:
            print(f"   FAIL: SpotFleet template creation failed: {str(e)}")
            import traceback

            traceback.print_exc()
            return False

    except ImportError as e:
        print(f"   FAIL: Import error: {str(e)}")
        return False


def test_request_creation():
    """Test Request creation."""
    try:
        from domain.request.aggregate import Request
        from domain.request.value_objects import RequestType

        print("   Testing Request creation...")

        # Test request creation
        try:
            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id="test-template",
                machine_count=2,
                provider_type="aws",
            )

            print(f"   PASS: Request created: {request.request_id}")
            print(f"   Request Type: {request.request_type}")
            print(f"   Template ID: {request.template_id}")
            print(f"   Machine Count: {request.requested_count}")
            print(f"   Provider Type: {request.provider_type}")

            return True

        except Exception as e:
            print(f"   FAIL: Request creation failed: {str(e)}")
            import traceback

            traceback.print_exc()
            return False

    except ImportError as e:
        print(f"   FAIL: Import error: {str(e)}")
        return False


def test_base_handler_validation():
    """Test base handler validation logic."""
    try:
        from unittest.mock import Mock

        from providers.aws.domain.template.aggregate import AWSTemplate
        from providers.aws.domain.template.value_objects import (
            AWSFleetType,
            ProviderApi,
        )

        print("   Testing base handler validation...")

        # Create a concrete handler for testing (using SpotFleetHandler)
        from providers.aws.infrastructure.handlers.spot_fleet_handler import (
            SpotFleetHandler,
        )

        # Create mocks
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

        # Test validation with valid template
        try:
            template = AWSTemplate(
                template_id="validation-test",
                provider_api=ProviderApi.SPOT_FLEET,
                instance_type="t2.micro",
                image_id="ami-12345",
                subnet_ids=["subnet-12345"],
                security_group_ids=["sg-12345"],
                fleet_role="AWSServiceRoleForEC2SpotFleet",
                fleet_type=AWSFleetType.REQUEST,
            )

            # Call the validation method directly
            handler._validate_prerequisites(template)
            print("   PASS: Base handler validation passed")

            return True

        except Exception as e:
            print(f"   FAIL: Base handler validation failed: {str(e)}")

            # Try to get more detailed error information
            if hasattr(e, "errors") and e.errors:
                print(f"   Validation errors: {e.errors}")

            # Let's also check what fields the template actually has
            print("   Template fields:")
            for field_name in dir(template):
                if not field_name.startswith("_") and not callable(getattr(template, field_name)):
                    value = getattr(template, field_name)
                    print(f"      {field_name}: {value}")

            import traceback

            traceback.print_exc()
            return False

    except ImportError as e:
        print(f"   FAIL: Import error: {str(e)}")
        return False


if __name__ == "__main__":
    success = test_domain_validation()
    sys.exit(0 if success else 1)
