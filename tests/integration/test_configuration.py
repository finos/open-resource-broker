#!/usr/bin/env python3
"""
Phase 5: Configuration Updates Test

This test validates that the configuration updates for launch template
management are working correctly.
"""

import json
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath("."))


def test_phase5_configuration():
    """Test Phase 5 configuration updates."""

    print("🔧 PHASE 5: CONFIGURATION UPDATES TEST")
    print("=" * 60)

    results = {
        "default_config_validation": False,
        "launch_template_config_present": False,
        "aws_provider_config_class": False,
        "configuration_loading": False,
    }

    try:
        # Test 1: Default configuration validation
        print("\n1️⃣ Testing Default Configuration...")
        results["default_config_validation"] = test_default_config_validation()

        # Test 2: Launch template configuration presence
        print("\n2️⃣ Testing Launch Template Configuration...")
        results["launch_template_config_present"] = (
            test_launch_template_config_present()
        )

        # Test 3: AWS provider configuration class
        print("\n3️⃣ Testing AWS Provider Configuration Class...")
        results["aws_provider_config_class"] = test_aws_provider_config_class()

        # Test 4: Configuration loading
        print("\n4️⃣ Testing Configuration Loading...")
        results["configuration_loading"] = test_configuration_loading()

        # Summary
        print("\n" + "=" * 60)
        print("📊 PHASE 5 CONFIGURATION TEST RESULTS")
        print("=" * 60)

        passed = sum(1 for result in results.values() if result)
        total = len(results)

        for test_name, result in results.items():
            status = "PASS: PASS" if result else "FAIL: FAIL"
            print(f"{test_name.replace('_', ' ').title()}: {status}")

        print(f"\nOverall: {passed}/{total} tests passed")

        if passed == total:
            print("🎉 ALL PHASE 5 CONFIGURATION TESTS PASSED!")
            return True
        else:
            print("WARN:  Some configuration tests failed")
            return False

    except Exception as e:
        print(f"FAIL: Test execution failed: {str(e)}")
        import traceback

        traceback.print_exc()
        return False


def test_default_config_validation():
    """Test that default configuration file is valid JSON and contains expected structure."""
    try:
        print("   Testing default configuration file validation...")

        # Load and parse the configuration file
        with open("config/default_config.json", "r") as f:
            config = json.load(f)

        # Validate basic structure
        if "provider" not in config:
            print("   FAIL: Missing 'provider' section in config")
            return False

        if "provider_defaults" not in config["provider"]:
            print("   FAIL: Missing 'provider_defaults' section")
            return False

        if "aws" not in config["provider"]["provider_defaults"]:
            print("   FAIL: Missing 'aws' provider defaults")
            return False

        print("   PASS: Default configuration file is valid JSON")
        print("   PASS: Basic configuration structure is present")

        return True

    except json.JSONDecodeError as e:
        print(f"   FAIL: Configuration file is not valid JSON: {str(e)}")
        return False
    except FileNotFoundError:
        print("   FAIL: Configuration file not found")
        return False
    except Exception as e:
        print(f"   FAIL: Configuration validation failed: {str(e)}")
        return False


def test_launch_template_config_present():
    """Test that launch template configuration is present in default config."""
    try:
        print("   Testing launch template configuration presence...")

        # Load configuration
        with open("config/default_config.json", "r") as f:
            config = json.load(f)

        # Check for launch template configuration
        aws_defaults = config["provider"]["provider_defaults"]["aws"]

        if "launch_template" not in aws_defaults:
            print("   FAIL: Launch template configuration not found in AWS defaults")
            return False

        lt_config = aws_defaults["launch_template"]

        # Validate expected fields
        expected_fields = [
            "create_per_request",
            "naming_strategy",
            "version_strategy",
            "reuse_existing",
            "cleanup_old_versions",
            "max_versions_per_template",
        ]

        for field in expected_fields:
            if field not in lt_config:
                print(f"   FAIL: Missing launch template field: {field}")
                return False

        print("   PASS: Launch template configuration found")
        print(f"   📋 Create per request: {lt_config['create_per_request']}")
        print(f"   📋 Naming strategy: {lt_config['naming_strategy']}")
        print(f"   📋 Version strategy: {lt_config['version_strategy']}")
        print(f"   📋 Reuse existing: {lt_config['reuse_existing']}")
        print(f"   📋 Max versions: {lt_config['max_versions_per_template']}")

        return True

    except Exception as e:
        print(f"   FAIL: Launch template config test failed: {str(e)}")
        return False


def test_aws_provider_config_class():
    """Test that AWS provider configuration class includes launch template config."""
    try:
        print("   Testing AWS provider configuration class...")

        # Import the configuration classes
        from src.providers.aws.configuration.config import (
            AWSProviderConfig,
            LaunchTemplateConfiguration,
        )

        # Test LaunchTemplateConfiguration class
        lt_config = LaunchTemplateConfiguration()

        # Validate default values
        if not lt_config.create_per_request:
            print(
                "   FAIL: LaunchTemplateConfiguration default create_per_request should be True"
            )
            return False

        if lt_config.naming_strategy != "request_based":
            print(
                "   FAIL: LaunchTemplateConfiguration default naming_strategy should be 'request_based'"
            )
            return False

        if lt_config.max_versions_per_template != 10:
            print(
                "   FAIL: LaunchTemplateConfiguration default max_versions_per_template should be 10"
            )
            return False

        print("   PASS: LaunchTemplateConfiguration class working")

        # Test AWSProviderConfig includes launch template
        # Need to provide authentication for validation
        aws_config = AWSProviderConfig(profile="default")

        if not hasattr(aws_config, "launch_template"):
            print("   FAIL: AWSProviderConfig missing launch_template field")
            return False

        if not isinstance(aws_config.launch_template, LaunchTemplateConfiguration):
            print(
                "   FAIL: AWSProviderConfig.launch_template is not LaunchTemplateConfiguration instance"
            )
            return False

        print("   PASS: AWSProviderConfig includes launch template configuration")
        print(
            f"   📋 Launch template config type: {type(aws_config.launch_template).__name__}"
        )

        return True

    except ImportError as e:
        print(f"   FAIL: Import error: {str(e)}")
        return False
    except Exception as e:
        print(f"   FAIL: AWS provider config class test failed: {str(e)}")
        return False


def test_configuration_loading():
    """Test that configuration can be loaded and parsed correctly."""
    try:
        print("   Testing configuration loading...")

        # Test loading configuration through the system
        from src.providers.aws.configuration.config import AWSProviderConfig

        # Load from JSON file
        with open("config/default_config.json", "r") as f:
            config_data = json.load(f)

        # Extract AWS provider defaults
        aws_defaults = config_data["provider"]["provider_defaults"]["aws"]

        # Create AWSProviderConfig from the loaded data
        # Note: We need to provide required fields for validation
        aws_config_data = {"region": "us-east-1", "profile": "default", **aws_defaults}

        aws_config = AWSProviderConfig(**aws_config_data)

        # Validate launch template configuration was loaded
        if not aws_config.launch_template:
            print("   FAIL: Launch template configuration not loaded")
            return False

        if not aws_config.launch_template.create_per_request:
            print("   FAIL: Launch template create_per_request not loaded correctly")
            return False

        print("   PASS: Configuration loading successful")
        print(f"   📋 Loaded region: {aws_config.region}")
        print(
            f"   📋 Launch template create_per_request: {aws_config.launch_template.create_per_request}"
        )
        print(
            f"   📋 Launch template naming_strategy: {aws_config.launch_template.naming_strategy}"
        )

        return True

    except Exception as e:
        print(f"   FAIL: Configuration loading test failed: {str(e)}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_phase5_configuration()
    sys.exit(0 if success else 1)
