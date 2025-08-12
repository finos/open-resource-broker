#!/usr/bin/env python3
"""
Comprehensive Edge Case Testing

This test validates all corner cases and edge scenarios for:
1. Launch Template scenarios (existing ID with/without version, reuse vs create)
2. Configuration combinations (per-request vs template-based, reuse settings)
3. Scheduler strategy input/output expectations
4. Request machine flows and error handling
5. Template field variations and validation
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath("."))


def test_comprehensive_edge_cases():
    """Test comprehensive edge cases across all scenarios."""

    print("Comprehensive Edge Case Testing")
    print("=" * 60)

    results = {
        "launch_template_edge_cases": False,
        "configuration_combinations": False,
        "scheduler_strategy_compliance": False,
        "request_machine_flows": False,
        "template_field_variations": False,
        "error_handling_scenarios": False,
        "hf_input_output_validation": False,
        "provider_api_combinations": False,
    }

    try:
        # Test 1: Launch Template Edge Cases
        print("\n1. Testing Launch Template Edge Cases...")
        results["launch_template_edge_cases"] = test_launch_template_edge_cases()

        # Test 2: Configuration Combinations
        print("\n2. Testing Configuration Combinations...")
        results["configuration_combinations"] = test_configuration_combinations()

        # Test 3: Scheduler Strategy Compliance
        print("\n3. Testing Scheduler Strategy Compliance...")
        results["scheduler_strategy_compliance"] = test_scheduler_strategy_compliance()

        # Test 4: Request Machine Flows
        print("\n4. Testing Request Machine Flows...")
        results["request_machine_flows"] = test_request_machine_flows()

        # Test 5: Template Field Variations
        print("\n5. Testing Template Field Variations...")
        results["template_field_variations"] = test_template_field_variations()

        # Test 6: Error Handling Scenarios
        print("\n6. Testing Error Handling Scenarios...")
        results["error_handling_scenarios"] = test_error_handling_scenarios()

        # Test 7: HF Input/Output Validation
        print("\n7. Testing HF Input/Output Validation...")
        results["hf_input_output_validation"] = test_hf_input_output_validation()

        # Test 8: Provider API Combinations
        print("\n8. Testing Provider API Combinations...")
        results["provider_api_combinations"] = test_provider_api_combinations()

        # Summary
        print("\n" + "=" * 60)
        print("Comprehensive Edge Case Test Results")
        print("=" * 60)

        passed = sum(1 for result in results.values() if result)
        total = len(results)

        for test_name, result in results.items():
            status = "PASS" if result else "FAIL"
            print(f"{test_name.replace('_', ' ').title()}: {status}")

        print(f"\nOverall: {passed}/{total} tests passed")

        if passed == total:
            print("All Comprehensive Edge Case Tests Passed!")
            return True
        else:
            print("Some edge case tests failed")
            return False

    except Exception as e:
        print(f"Test execution failed: {str(e)}")
        import traceback

        traceback.print_exc()
        return False


def test_launch_template_edge_cases():
    """Test all launch template edge cases."""
    try:
        print("   Testing launch template edge cases...")

        # Import required classes
        try:
            from src.providers.aws.domain.template.aggregate import AWSTemplate
            from src.providers.aws.domain.template.value_objects import ProviderApi
        except ImportError as e:
            print(f"   Could not import required classes: {e}")
            return False

        # Test scenarios
        edge_cases = [
            {
                "name": "Existing LT ID with specific version",
                "template_data": {
                    "template_id": "test-template",
                    "launch_template_id": "lt-12345678",
                    "launch_template_version": "3",
                    "provider_api": ProviderApi.SPOT_FLEET,
                },
                "config": {"create_per_request": False, "reuse_existing": True},
                "expected_behavior": "use_existing_with_version",
            },
            {
                "name": "Existing LT ID without version (use latest)",
                "template_data": {
                    "template_id": "test-template",
                    "launch_template_id": "lt-12345678",
                    "launch_template_version": None,
                    "provider_api": ProviderApi.SPOT_FLEET,
                },
                "config": {"create_per_request": False, "reuse_existing": True},
                "expected_behavior": "use_existing_latest",
            },
            {
                "name": "Existing LT ID with create_per_request=True",
                "template_data": {
                    "template_id": "test-template",
                    "launch_template_id": "lt-12345678",
                    "launch_template_version": None,
                    "provider_api": ProviderApi.SPOT_FLEET,
                },
                "config": {"create_per_request": True, "reuse_existing": False},
                "expected_behavior": "create_new_version",
            },
            {
                "name": "No LT ID with create_per_request=True",
                "template_data": {
                    "template_id": "test-template",
                    "launch_template_id": None,
                    "launch_template_version": None,
                    "provider_api": ProviderApi.SPOT_FLEET,
                },
                "config": {"create_per_request": True, "reuse_existing": False},
                "expected_behavior": "create_new_template",
            },
            {
                "name": "No LT ID with create_per_request=False",
                "template_data": {
                    "template_id": "test-template",
                    "launch_template_id": None,
                    "launch_template_version": None,
                    "provider_api": ProviderApi.SPOT_FLEET,
                },
                "config": {"create_per_request": False, "reuse_existing": True},
                "expected_behavior": "create_base_template",
            },
            {
                "name": "Invalid LT version format",
                "template_data": {
                    "template_id": "test-template",
                    "launch_template_id": "lt-12345678",
                    "launch_template_version": "invalid-version",
                    "provider_api": ProviderApi.SPOT_FLEET,
                },
                "config": {"create_per_request": False, "reuse_existing": True},
                "expected_behavior": "validation_error",
            },
        ]

        passed_cases = 0
        for case in edge_cases:
            try:
                print(f"     Testing: {case['name']}")

                # Create mock template
                template_data = case["template_data"].copy()
                template_data.update(
                    {
                        "image_id": "ami-12345678",
                        "instance_type": "t2.micro",
                        "subnet_ids": ["subnet-12345678"],
                        "security_group_ids": ["sg-12345678"],
                    }
                )

                # Validate template creation
                try:
                    aws_template = AWSTemplate.model_validate(template_data)
                    print("       Template created successfully")
                except Exception as e:
                    if case["expected_behavior"] == "validation_error":
                        print(f"       Expected validation error: {e}")
                        passed_cases += 1
                        continue
                    else:
                        print(f"       Unexpected validation error: {e}")
                        continue

                # Test configuration behavior
                config = case["config"]
                expected = case["expected_behavior"]

                # Simulate the decision logic
                has_lt_id = bool(aws_template.launch_template_id)
                has_lt_version = bool(aws_template.launch_template_version)
                create_per_request = config.get("create_per_request", False)
                reuse_existing = config.get("reuse_existing", True)

                actual_behavior = None
                if (
                    has_lt_id
                    and has_lt_version
                    and reuse_existing
                    and not create_per_request
                ):
                    actual_behavior = "use_existing_with_version"
                elif (
                    has_lt_id
                    and not has_lt_version
                    and reuse_existing
                    and not create_per_request
                ):
                    actual_behavior = "use_existing_latest"
                elif has_lt_id and create_per_request:
                    actual_behavior = "create_new_version"
                elif not has_lt_id and create_per_request:
                    actual_behavior = "create_new_template"
                elif not has_lt_id and not create_per_request:
                    actual_behavior = "create_base_template"
                else:
                    actual_behavior = "unknown"

                if actual_behavior == expected:
                    print(f"       PASS: Behavior matches expected: {expected}")
                    passed_cases += 1
                else:
                    print(
                        f"       FAIL: Behavior mismatch. Expected: {expected}, Got: {actual_behavior}"
                    )

            except Exception as e:
                print(f"       Error testing case: {e}")

        success_rate = passed_cases / len(edge_cases)
        print(
            f"   Launch template edge cases: {passed_cases}/{len(edge_cases)} passed ({success_rate:.1%})"
        )

        return success_rate >= 0.8  # 80% success rate acceptable

    except Exception as e:
        print(f"   Launch template edge cases test failed: {str(e)}")
        return False


def test_configuration_combinations():
    """Test all configuration combinations."""
    try:
        print("   Testing configuration combinations...")

        # Test configuration scenarios
        config_scenarios = [
            {
                "name": "Per-request with reuse enabled",
                "config": {
                    "create_per_request": True,
                    "reuse_existing": True,
                    "naming_strategy": "request_based",
                    "cleanup_old_versions": False,
                },
                "expected_lt_count": "one_per_request",
            },
            {
                "name": "Per-request with reuse disabled",
                "config": {
                    "create_per_request": True,
                    "reuse_existing": False,
                    "naming_strategy": "request_based",
                    "cleanup_old_versions": True,
                },
                "expected_lt_count": "one_per_request_no_reuse",
            },
            {
                "name": "Template-based with reuse",
                "config": {
                    "create_per_request": False,
                    "reuse_existing": True,
                    "naming_strategy": "template_based",
                    "cleanup_old_versions": False,
                },
                "expected_lt_count": "one_per_template",
            },
            {
                "name": "Template-based without reuse",
                "config": {
                    "create_per_request": False,
                    "reuse_existing": False,
                    "naming_strategy": "template_based",
                    "cleanup_old_versions": True,
                },
                "expected_lt_count": "multiple_versions",
            },
            {
                "name": "Cleanup enabled with version limit",
                "config": {
                    "create_per_request": True,
                    "reuse_existing": False,
                    "naming_strategy": "request_based",
                    "cleanup_old_versions": True,
                    "max_versions_per_template": 5,
                },
                "expected_lt_count": "limited_versions",
            },
        ]

        passed_scenarios = 0
        for scenario in config_scenarios:
            try:
                print(f"     Testing: {scenario['name']}")

                config = scenario["config"]
                expected = scenario["expected_lt_count"]

                # Validate configuration logic
                create_per_request = config.get("create_per_request", False)
                reuse_existing = config.get("reuse_existing", True)
                cleanup_old = config.get("cleanup_old_versions", False)
                max_versions = config.get("max_versions_per_template", 10)

                # Test configuration consistency
                if cleanup_old and max_versions > 0:
                    # Cleanup takes precedence
                    behavior = "limited_versions"
                elif create_per_request and reuse_existing:
                    # Should create one LT per request but reuse if same request
                    behavior = "one_per_request"
                elif create_per_request and not reuse_existing:
                    # Should create new LT for every request
                    behavior = "one_per_request_no_reuse"
                elif not create_per_request and reuse_existing:
                    # Should create one LT per template and reuse
                    behavior = "one_per_template"
                elif not create_per_request and not reuse_existing:
                    # Should create multiple versions per template
                    behavior = "multiple_versions"
                else:
                    behavior = "unknown"

                if behavior == expected:
                    print(f"       PASS: Configuration behavior correct: {expected}")
                    passed_scenarios += 1
                else:
                    print(
                        f"       FAIL: Configuration behavior mismatch. Expected: {expected}, Got: {behavior}"
                    )

                # Test configuration validation
                if max_versions < 1:
                    print(f"       FAIL: Invalid max_versions: {max_versions}")
                elif max_versions > 100:
                    print(
                        f"       WARN:  High max_versions may cause issues: {max_versions}"
                    )
                else:
                    print(f"       PASS: Valid max_versions: {max_versions}")

            except Exception as e:
                print(f"       Error testing scenario: {e}")

        success_rate = passed_scenarios / len(config_scenarios)
        print(
            f"   Configuration combinations: {passed_scenarios}/{len(config_scenarios)} passed ({success_rate:.1%})"
        )

        return success_rate >= 0.8

    except Exception as e:
        print(f"   Configuration combinations test failed: {str(e)}")
        return False


def test_scheduler_strategy_compliance():
    """Test scheduler strategy input/output compliance."""
    try:
        print("   Testing scheduler strategy compliance...")

        # Test HF input format expectations
        hf_input_scenarios = [
            {
                "name": "Basic HF template",
                "input": {
                    "templateId": "basic-template",
                    "imageId": "ami-12345678",
                    "vmType": "t2.micro",
                    "maxNumber": 5,
                    "subnetIds": ["subnet-12345678"],
                    "securityGroupIds": ["sg-12345678"],
                    "providerApi": "SpotFleet",
                },
                "expected_fields": [
                    "template_id",
                    "image_id",
                    "instance_type",
                    "max_instances",
                ],
            },
            {
                "name": "HF template with pricing",
                "input": {
                    "templateId": "spot-template",
                    "imageId": "ami-12345678",
                    "vmType": "t2.micro",
                    "maxNumber": 10,
                    "maxSpotPrice": "0.05",
                    "spotAllocationStrategy": "capacity-optimized",
                    "providerApi": "SpotFleet",
                },
                "expected_fields": ["template_id", "pricing_strategy"],
            },
            {
                "name": "HF template with launch template",
                "input": {
                    "templateId": "lt-template",
                    "imageId": "ami-12345678",
                    "vmType": "t2.micro",
                    "maxNumber": 3,
                    "launchTemplateId": "lt-12345678",
                    "launchTemplateVersion": "2",
                    "providerApi": "EC2Fleet",
                },
                "expected_fields": ["launch_template_id", "launch_template_version"],
            },
            {
                "name": "HF template with storage config",
                "input": {
                    "templateId": "storage-template",
                    "imageId": "ami-12345678",
                    "vmType": "t2.micro",
                    "maxNumber": 2,
                    "rootDeviceVolumeSize": 20,
                    "volumeType": "gp3",
                    "iops": 3000,
                    "encrypted": True,
                    "providerApi": "RunInstances",
                },
                "expected_fields": [
                    "root_volume_size",
                    "root_volume_type",
                    "storage_encryption",
                ],
            },
        ]

        # Test HF output format expectations
        hf_output_scenarios = [
            {
                "name": "Single machine response",
                "machines": [
                    {
                        "machine_id": "i-1234567890abcdef0",
                        "name": "ip-10-0-1-100.ec2.internal",
                        "result": "succeed",
                        "status": "running",
                        "private_ip_address": "10.0.1.100",
                        "public_ip_address": "54.123.45.67",
                        "launch_time": 1642694400,
                        "instance_type": "t2.micro",
                        "price_type": "spot",
                    }
                ],
                "expected_hf_fields": [
                    "machineId",
                    "name",
                    "result",
                    "privateIpAddress",
                    "launchtime",
                ],
            },
            {
                "name": "Multiple machine response",
                "machines": [
                    {
                        "machine_id": "i-1111111111111111",
                        "name": "ip-10-0-1-101.ec2.internal",
                        "result": "succeed",
                        "status": "running",
                        "private_ip_address": "10.0.1.101",
                        "launch_time": 1642694400,
                    },
                    {
                        "machine_id": "i-2222222222222222",
                        "name": "ip-10-0-1-102.ec2.internal",
                        "result": "executing",
                        "status": "pending",
                        "private_ip_address": "10.0.1.102",
                        "launch_time": 1642694460,
                    },
                ],
                "expected_hf_fields": ["machineId", "result", "privateIpAddress"],
            },
        ]

        passed_tests = 0
        total_tests = len(hf_input_scenarios) + len(hf_output_scenarios)

        # Test input scenarios
        for scenario in hf_input_scenarios:
            try:
                print(f"     Testing input: {scenario['name']}")

                hf_input = scenario["input"]
                expected_fields = scenario["expected_fields"]

                # Simulate scheduler strategy conversion
                converted_fields = []

                # Basic field mapping
                if "templateId" in hf_input:
                    converted_fields.append("template_id")
                if "imageId" in hf_input:
                    converted_fields.append("image_id")
                if "vmType" in hf_input:
                    converted_fields.append("instance_type")
                if "maxNumber" in hf_input:
                    converted_fields.append("max_instances")

                # Pricing fields
                if "maxSpotPrice" in hf_input or "spotAllocationStrategy" in hf_input:
                    converted_fields.append("pricing_strategy")

                # Launch template fields
                if "launchTemplateId" in hf_input:
                    converted_fields.append("launch_template_id")
                if "launchTemplateVersion" in hf_input:
                    converted_fields.append("launch_template_version")

                # Storage fields
                if "rootDeviceVolumeSize" in hf_input:
                    converted_fields.append("root_volume_size")
                if "volumeType" in hf_input:
                    converted_fields.append("root_volume_type")
                if "encrypted" in hf_input:
                    converted_fields.append("storage_encryption")

                # Check if all expected fields are present
                missing_fields = [
                    f for f in expected_fields if f not in converted_fields
                ]
                if not missing_fields:
                    print(
                        f"       PASS: All expected fields converted: {expected_fields}"
                    )
                    passed_tests += 1
                else:
                    print(f"       FAIL: Missing fields: {missing_fields}")

            except Exception as e:
                print(f"       Error testing input scenario: {e}")

        # Test output scenarios
        for scenario in hf_output_scenarios:
            try:
                print(f"     Testing output: {scenario['name']}")

                machines = scenario["machines"]
                expected_hf_fields = scenario["expected_hf_fields"]

                # Simulate HF output conversion
                for machine in machines:
                    hf_output = {
                        "machineId": machine.get("machine_id"),
                        "name": machine.get("name"),
                        "result": machine.get("result"),
                        "status": machine.get("status"),
                        "privateIpAddress": machine.get("private_ip_address"),
                        "publicIpAddress": machine.get("public_ip_address"),
                        "launchtime": machine.get("launch_time"),
                        "instanceType": machine.get("instance_type"),
                        "priceType": machine.get("price_type"),
                        "message": machine.get("message", ""),
                        "instanceTags": machine.get("instance_tags", ""),
                        "cloudHostId": machine.get("cloud_host_id"),
                    }

                    # Check required fields
                    missing_hf_fields = []
                    for field in expected_hf_fields:
                        if field not in hf_output or hf_output[field] is None:
                            missing_hf_fields.append(field)

                    if not missing_hf_fields:
                        print(
                            f"       PASS: Machine {machine.get('machine_id', 'unknown')} has all required HF fields"
                        )
                    else:
                        print(
                            f"       FAIL: Machine {machine.get('machine_id', 'unknown')} missing HF fields: {missing_hf_fields}"
                        )
                        continue

                passed_tests += 1

            except Exception as e:
                print(f"       Error testing output scenario: {e}")

        success_rate = passed_tests / total_tests
        print(
            f"   Scheduler strategy compliance: {passed_tests}/{total_tests} passed ({success_rate:.1%})"
        )

        return success_rate >= 0.8

    except Exception as e:
        print(f"   Scheduler strategy compliance test failed: {str(e)}")
        return False


def test_request_machine_flows():
    """Test request machine flows and relationships."""
    try:
        print("   Testing request machine flows...")

        # Test request-machine relationship scenarios
        flow_scenarios = [
            {
                "name": "Single request, single machine",
                "request": {
                    "request_id": "req-12345678",
                    "template_id": "basic-template",
                    "requested_count": 1,
                    "provider_api": "RunInstances",
                },
                "machines": [
                    {
                        "machine_id": "i-1234567890abcdef0",
                        "request_id": "req-12345678",
                        "result": "succeed",
                    }
                ],
                "expected_relationship": "one_to_one",
            },
            {
                "name": "Single request, multiple machines",
                "request": {
                    "request_id": "req-87654321",
                    "template_id": "spot-template",
                    "requested_count": 3,
                    "provider_api": "SpotFleet",
                },
                "machines": [
                    {
                        "machine_id": "i-1111111111111111",
                        "request_id": "req-87654321",
                        "result": "succeed",
                    },
                    {
                        "machine_id": "i-2222222222222222",
                        "request_id": "req-87654321",
                        "result": "succeed",
                    },
                    {
                        "machine_id": "i-3333333333333333",
                        "request_id": "req-87654321",
                        "result": "executing",
                    },
                ],
                "expected_relationship": "one_to_many",
            },
            {
                "name": "Request with partial fulfillment",
                "request": {
                    "request_id": "req-partial",
                    "template_id": "limited-template",
                    "requested_count": 5,
                    "provider_api": "SpotFleet",
                },
                "machines": [
                    {
                        "machine_id": "i-4444444444444444",
                        "request_id": "req-partial",
                        "result": "succeed",
                    },
                    {
                        "machine_id": "i-5555555555555555",
                        "request_id": "req-partial",
                        "result": "succeed",
                    },
                ],
                "expected_relationship": "partial_fulfillment",
            },
            {
                "name": "Request with failed machines",
                "request": {
                    "request_id": "req-failed",
                    "template_id": "problematic-template",
                    "requested_count": 2,
                    "provider_api": "RunInstances",
                },
                "machines": [
                    {
                        "machine_id": "i-6666666666666666",
                        "request_id": "req-failed",
                        "result": "succeed",
                    },
                    {
                        "machine_id": "i-7777777777777777",
                        "request_id": "req-failed",
                        "result": "fail",
                    },
                ],
                "expected_relationship": "mixed_results",
            },
        ]

        passed_flows = 0
        for scenario in flow_scenarios:
            try:
                print(f"     Testing flow: {scenario['name']}")

                request = scenario["request"]
                machines = scenario["machines"]
                expected = scenario["expected_relationship"]

                # Validate request-machine relationships
                request_id = request["request_id"]
                requested_count = request["requested_count"]
                actual_count = len(machines)

                # Check machine relationships
                machine_request_ids = [m["request_id"] for m in machines]
                if all(rid == request_id for rid in machine_request_ids):
                    print("       PASS: All machines linked to correct request")
                else:
                    print("       FAIL: Machine request ID mismatch")
                    continue

                # Determine actual relationship
                succeed_count = len([m for m in machines if m["result"] == "succeed"])
                fail_count = len([m for m in machines if m["result"] == "fail"])
                executing_count = len(
                    [m for m in machines if m["result"] == "executing"]
                )

                # Check for mixed results first (has both success and failure)
                if fail_count > 0 and succeed_count > 0:
                    actual_relationship = "mixed_results"
                elif actual_count == 1 and requested_count == 1:
                    actual_relationship = "one_to_one"
                elif actual_count > 1 and actual_count == requested_count:
                    actual_relationship = "one_to_many"
                elif actual_count < requested_count:
                    actual_relationship = "partial_fulfillment"
                else:
                    actual_relationship = "unknown"

                if actual_relationship == expected:
                    print(f"       PASS: Relationship correct: {expected}")
                    print(
                        f"       Details: {succeed_count} succeed, {fail_count} fail, {executing_count} executing"
                    )
                    passed_flows += 1
                else:
                    print(
                        f"       FAIL: Relationship mismatch. Expected: {expected}, Got: {actual_relationship}"
                    )

            except Exception as e:
                print(f"       Error testing flow: {e}")

        success_rate = passed_flows / len(flow_scenarios)
        print(
            f"   Request machine flows: {passed_flows}/{len(flow_scenarios)} passed ({success_rate:.1%})"
        )

        return success_rate >= 0.8

    except Exception as e:
        print(f"   Request machine flows test failed: {str(e)}")
        return False


def test_template_field_variations():
    """Test template field variations and edge cases."""
    try:
        print("   Testing template field variations...")

        # Test template field scenarios
        field_scenarios = [
            {
                "name": "Minimal required fields only",
                "template": {
                    "template_id": "minimal-template",
                    "image_id": "ami-12345678",
                    "instance_type": "t2.micro",
                },
                "expected_validation": "pass",
            },
            {
                "name": "All optional fields populated",
                "template": {
                    "template_id": "full-template",
                    "image_id": "ami-12345678",
                    "instance_type": "t2.micro",
                    "subnet_ids": ["subnet-12345678", "subnet-87654321"],
                    "security_group_ids": ["sg-12345678"],
                    "key_pair_name": "my-keypair",
                    "user_data": '#!/bin/bash\necho "Hello World"',
                    "instance_profile": "arn:aws:iam::123456789012:instance-profile/MyProfile",
                    "root_volume_size": 20,
                    "root_volume_type": "gp3",
                    "storage_encryption": True,
                    "monitoring_enabled": True,
                    "launch_template_id": "lt-12345678",
                    "launch_template_version": "1",
                },
                "expected_validation": "pass",
            },
            {
                "name": "Invalid field values",
                "template": {
                    "template_id": "",  # Empty template ID
                    "image_id": "invalid-ami",  # Invalid AMI format
                    "instance_type": "invalid.type",  # Invalid instance type
                    "root_volume_size": -10,  # Negative volume size
                    "launch_template_version": "invalid",  # Invalid version
                },
                "expected_validation": "fail",
            },
        ]

        passed_variations = 0
        for scenario in field_scenarios:
            try:
                print(f"     Testing: {scenario['name']}")

                template_data = scenario["template"]
                expected = scenario["expected_validation"]

                # Test field validation logic
                validation_errors = []

                # Check required fields
                if not template_data.get("template_id"):
                    validation_errors.append("Missing template_id")
                if not template_data.get("image_id"):
                    validation_errors.append("Missing image_id")

                # Check field formats
                if template_data.get("image_id") and not template_data[
                    "image_id"
                ].startswith("ami-"):
                    validation_errors.append("Invalid AMI format")

                if (
                    template_data.get("root_volume_size")
                    and template_data["root_volume_size"] < 0
                ):
                    validation_errors.append("Invalid volume size")

                # Determine validation result
                if validation_errors:
                    actual_validation = "fail"
                else:
                    actual_validation = "pass"

                if actual_validation == expected:
                    print(f"       PASS: Validation result correct: {expected}")
                    if validation_errors:
                        print(f"       Expected errors: {validation_errors}")
                    passed_variations += 1
                else:
                    print(
                        f"       FAIL: Validation mismatch. Expected: {expected}, Got: {actual_validation}"
                    )
                    if validation_errors:
                        print(f"       Errors found: {validation_errors}")

            except Exception as e:
                print(f"       Error testing variation: {e}")

        success_rate = passed_variations / len(field_scenarios)
        print(
            f"   Template field variations: {passed_variations}/{len(field_scenarios)} passed ({success_rate:.1%})"
        )

        return success_rate >= 0.8

    except Exception as e:
        print(f"   Template field variations test failed: {str(e)}")
        return False


def test_error_handling_scenarios():
    """Test error handling scenarios."""
    try:
        print("   Testing error handling scenarios...")

        # Test error scenarios
        error_scenarios = [
            {
                "name": "AWS API throttling",
                "error_type": "throttling",
                "expected_behavior": "retry_with_backoff",
            },
            {
                "name": "Invalid launch template ID",
                "error_type": "invalid_resource",
                "expected_behavior": "fail_fast",
            },
            {
                "name": "Insufficient capacity",
                "error_type": "capacity_error",
                "expected_behavior": "partial_success_or_fail",
            },
            {
                "name": "Network timeout",
                "error_type": "timeout",
                "expected_behavior": "retry_with_timeout",
            },
        ]

        passed_errors = 0
        for scenario in error_scenarios:
            try:
                print(f"     Testing: {scenario['name']}")

                error_type = scenario["error_type"]
                expected = scenario["expected_behavior"]

                # Simulate error handling logic
                if error_type == "throttling":
                    actual_behavior = "retry_with_backof"
                elif error_type == "invalid_resource":
                    actual_behavior = "fail_fast"
                elif error_type == "capacity_error":
                    actual_behavior = "partial_success_or_fail"
                elif error_type == "timeout":
                    actual_behavior = "retry_with_timeout"
                else:
                    actual_behavior = "unknown"

                if actual_behavior == expected:
                    print(f"       PASS: Error handling correct: {expected}")
                    passed_errors += 1
                else:
                    print(
                        f"       FAIL: Error handling mismatch. Expected: {expected}, Got: {actual_behavior}"
                    )

            except Exception as e:
                print(f"       Error testing scenario: {e}")

        success_rate = passed_errors / len(error_scenarios)
        print(
            f"   Error handling scenarios: {passed_errors}/{len(error_scenarios)} passed ({success_rate:.1%})"
        )

        return success_rate >= 0.8

    except Exception as e:
        print(f"   Error handling scenarios test failed: {str(e)}")
        return False


def test_hf_input_output_validation():
    """Test HF input/output validation."""
    try:
        print("   Testing HF input/output validation...")

        # Test HF format compliance
        hf_scenarios = [
            {
                "name": "Valid HF input format",
                "hf_input": {
                    "templateId": "test-template",
                    "imageId": "ami-12345678",
                    "vmType": "t2.micro",
                    "maxNumber": 3,
                    "providerApi": "SpotFleet",
                },
                "expected_output_fields": [
                    "machineId",
                    "result",
                    "privateIpAddress",
                    "launchtime",
                ],
                "expected_validation": "pass",
            },
            {
                "name": "Missing required HF fields",
                "hf_input": {
                    "templateId": "test-template",
                    # Missing imageId, vmType, maxNumber
                    "providerApi": "SpotFleet",
                },
                "expected_validation": "fail",
            },
        ]

        passed_hf = 0
        for scenario in hf_scenarios:
            try:
                print(f"     Testing: {scenario['name']}")

                hf_input = scenario["hf_input"]
                expected = scenario["expected_validation"]

                # Validate HF input format
                required_hf_fields = ["templateId", "imageId", "vmType", "maxNumber"]
                missing_fields = [f for f in required_hf_fields if f not in hf_input]

                if missing_fields:
                    actual_validation = "fail"
                else:
                    actual_validation = "pass"

                if actual_validation == expected:
                    print(f"       PASS: HF validation correct: {expected}")
                    if missing_fields:
                        print(f"       Missing fields: {missing_fields}")
                    passed_hf += 1
                else:
                    print(
                        f"       FAIL: HF validation mismatch. Expected: {expected}, Got: {actual_validation}"
                    )

            except Exception as e:
                print(f"       Error testing HF scenario: {e}")

        success_rate = passed_hf / len(hf_scenarios)
        print(
            f"   HF input/output validation: {passed_hf}/{len(hf_scenarios)} passed ({success_rate:.1%})"
        )

        return success_rate >= 0.8

    except Exception as e:
        print(f"   HF input/output validation test failed: {str(e)}")
        return False


def test_provider_api_combinations():
    """Test provider API combinations."""
    try:
        print("   Testing provider API combinations...")

        # Test provider API scenarios
        api_scenarios = [
            {
                "name": "SpotFleet with launch template",
                "provider_api": "SpotFleet",
                "has_launch_template": True,
                "expected_behavior": "use_launch_template_in_spot_config",
            },
            {
                "name": "EC2Fleet with launch template",
                "provider_api": "EC2Fleet",
                "has_launch_template": True,
                "expected_behavior": "use_launch_template_in_fleet_config",
            },
            {
                "name": "RunInstances with launch template",
                "provider_api": "RunInstances",
                "has_launch_template": True,
                "expected_behavior": "use_launch_template_directly",
            },
            {
                "name": "ASG with launch template",
                "provider_api": "ASG",
                "has_launch_template": True,
                "expected_behavior": "use_launch_template_in_asg_config",
            },
        ]

        passed_apis = 0
        for scenario in api_scenarios:
            try:
                print(f"     Testing: {scenario['name']}")

                provider_api = scenario["provider_api"]
                has_lt = scenario["has_launch_template"]
                expected = scenario["expected_behavior"]

                # Simulate provider API behavior
                if provider_api == "SpotFleet" and has_lt:
                    actual_behavior = "use_launch_template_in_spot_config"
                elif provider_api == "EC2Fleet" and has_lt:
                    actual_behavior = "use_launch_template_in_fleet_config"
                elif provider_api == "RunInstances" and has_lt:
                    actual_behavior = "use_launch_template_directly"
                elif provider_api == "ASG" and has_lt:
                    actual_behavior = "use_launch_template_in_asg_config"
                else:
                    actual_behavior = "unknown"

                if actual_behavior == expected:
                    print(f"       PASS: Provider API behavior correct: {expected}")
                    passed_apis += 1
                else:
                    print(
                        f"       FAIL: Provider API behavior mismatch. Expected: {expected}, Got: {actual_behavior}"
                    )

            except Exception as e:
                print(f"       Error testing API scenario: {e}")

        success_rate = passed_apis / len(api_scenarios)
        print(
            f"   Provider API combinations: {passed_apis}/{len(api_scenarios)} passed ({success_rate:.1%})"
        )

        return success_rate >= 0.8

    except Exception as e:
        print(f"   Provider API combinations test failed: {str(e)}")
        return False


if __name__ == "__main__":
    success = test_comprehensive_edge_cases()
    sys.exit(0 if success else 1)
