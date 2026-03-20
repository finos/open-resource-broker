#!/usr/bin/env python3
"""
Isolated Domain Validation Test

This test isolates domain model validation issues to understand what's failing
and ensure we're creating valid domain objects correctly.
"""

import os
import sys
import traceback

import pytest

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
        try:
            test_aws_template_basic()
            results["aws_template_basic"] = True
        except Exception as e:
            print(f"   FAIL: Basic AWSTemplate creation failed: {e!s}")
            traceback.print_exc()

        # SpotFleet AWSTemplate creation
        print("\n2. Testing SpotFleet AWSTemplate Creation...")
        try:
            test_aws_template_spot_fleet()
            results["aws_template_spot_fleet"] = True
        except Exception as e:
            print(f"   FAIL: SpotFleet AWSTemplate creation failed: {e!s}")
            traceback.print_exc()

        # Request creation
        print("\n3. Testing Request Creation...")
        try:
            test_request_creation()
            results["request_creation"] = True
        except Exception as e:
            print(f"   FAIL: Request creation failed: {e!s}")
            traceback.print_exc()

        # Base handler validation
        print("\n4. Testing Base Handler Validation...")
        try:
            test_base_handler_validation()
            results["base_handler_validation"] = True
        except Exception as e:
            print(f"   FAIL: Base handler validation failed: {e!s}")
            traceback.print_exc()

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
        else:
            print("WARN: Some domain validation tests failed")
            pytest.fail(f"Only {passed}/{total} domain validation tests passed")

    except Exception as e:
        print(f"FAIL: Test execution failed: {e!s}")
        traceback.print_exc()
        pytest.fail(f"Test execution failed: {e!s}")


def test_aws_template_basic():
    """Test basic AWSTemplate creation."""
    try:
        from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
        from orb.providers.aws.domain.template.value_objects import ProviderApi

        print("   Testing basic AWSTemplate creation...")

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

    except ImportError as e:
        pytest.fail(f"Import error: {e!s}")


def test_aws_template_spot_fleet():
    """Test SpotFleet AWSTemplate creation."""
    try:
        from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
        from orb.providers.aws.domain.template.value_objects import (
            AWSFleetType,
            ProviderApi,
        )

        print("   Testing SpotFleet AWSTemplate creation...")

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

    except ImportError as e:
        pytest.fail(f"Import error: {e!s}")


def test_request_creation():
    """Test Request creation."""
    try:
        from orb.domain.request.aggregate import Request
        from orb.domain.request.value_objects import RequestType

        print("   Testing Request creation...")

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

    except ImportError as e:
        pytest.fail(f"Import error: {e!s}")


def test_base_handler_validation():
    """Test base handler validation logic."""
    try:
        from unittest.mock import Mock

        from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
        from orb.providers.aws.domain.template.value_objects import (
            AWSFleetType,
            ProviderApi,
        )

        print("   Testing base handler validation...")

        from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import (
            SpotFleetHandler,
        )

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

        template = AWSTemplate(
            template_id="validation-test",
            provider_api=ProviderApi.SPOT_FLEET,
            instance_type="t2.micro",
            image_id="ami-12345",
            subnet_ids=["subnet-12345"],
            security_group_ids=["sg-12345"],
            fleet_role="AWSServiceRoleForEC2SpotFleet",
            fleet_type=AWSFleetType.REQUEST,
            machine_types={"t2.micro": 1},
        )

        handler._validate_prerequisites(template)
        print("   PASS: Base handler validation passed")

    except ImportError as e:
        pytest.fail(f"Import error: {e!s}")


if __name__ == "__main__":
    success = test_domain_validation()
    sys.exit(0 if success else 1)
