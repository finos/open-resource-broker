"""Tests asserting AWS-specific leaks are removed from interface/generic layers.

Covers:
- infrastructure_command_handler.py: no hardcoded 'us-east-1' or 'default' profile
- mcp/server/core.py: no 'ec2' default for template_type
- sdk/client.py: no 'aws' sentinel in provider parameter
- input_validator.py: validate_aws_region removed from generic layer
- providers/aws/validation/region_validator.py: validate_aws_region exists there
"""

import ast
import importlib
import inspect
from pathlib import Path

import pytest

SRC = Path(__file__).parents[3] / "src" / "orb"


# ---------------------------------------------------------------------------
# infrastructure_command_handler.py — no 'us-east-1' or 'default' literals
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInfrastructureCommandHandlerNoAWSLeaks:
    def _source(self) -> str:
        return (SRC / "interface" / "infrastructure_command_handler.py").read_text()

    def test_no_us_east_1_literal(self):
        assert "us-east-1" not in self._source(), (
            "infrastructure_command_handler.py must not contain hardcoded 'us-east-1'"
        )

    def test_no_hardcoded_default_profile_in_fallback(self):
        source = self._source()
        # The string 'default' must not appear as a dict value in a fallback config.
        # We check that the pattern "'profile': 'default'" is absent.
        assert "'profile': 'default'" not in source, (
            "infrastructure_command_handler.py must not contain hardcoded 'profile': 'default'"
        )

    def test_no_aws_type_guard_in_overrides(self):
        source = self._source()
        # The type == 'aws' guard that restricted override logic to AWS only must be gone.
        assert "== \"aws\"" not in source and "== 'aws'" not in source, (
            "_get_active_providers_with_overrides must not guard on provider type 'aws'"
        )

    def test_fallback_dict_has_no_region_key(self):
        """When no config file exists the fallback dict must not include 'region'."""
        import json
        from unittest.mock import patch

        from orb.interface.infrastructure_command_handler import _get_active_providers

        from unittest.mock import MagicMock

        mock_registry = MagicMock()
        mock_registry.get_registered_providers.return_value = ["mock"]
        with (
            patch(
                "orb.interface.infrastructure_command_handler.get_config_location",
                return_value=Path("/nonexistent_path_xyz"),
            ),
            patch(
                "orb.providers.registry.get_provider_registry",
                return_value=mock_registry,
            ),
        ):
            providers = _get_active_providers()

        assert len(providers) == 1
        assert "region" not in providers[0].get("config", {}), (
            "Fallback provider config must not contain a hardcoded 'region'"
        )
        assert "profile" not in providers[0].get("config", {}), (
            "Fallback provider config must not contain a hardcoded 'profile'"
        )


# ---------------------------------------------------------------------------
# mcp/server/core.py — no 'ec2' default for template_type
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMCPServerCoreNoEC2Default:
    def _source(self) -> str:
        return (SRC / "interface" / "mcp" / "server" / "core.py").read_text()

    def test_no_ec2_default_in_generate_provision_prompt(self):
        source = self._source()
        # The literal default "ec2" must not appear as a .get() fallback
        assert '.get("template_type", "ec2")' not in source, (
            "_generate_provision_prompt must not default template_type to 'ec2'"
        )
        assert ".get('template_type', 'ec2')" not in source, (
            "_generate_provision_prompt must not default template_type to 'ec2'"
        )

    def test_provision_prompt_uses_generic_label_when_no_type(self):
        from orb.interface.mcp.server.core import OpenResourceBrokerMCPServer

        server = OpenResourceBrokerMCPServer()
        # Call with no template_type argument — must not produce 'ec2' in output
        prompt = server._generate_provision_prompt({})
        assert "ec2" not in prompt, (
            "Provision prompt with no template_type must not mention 'ec2'"
        )

    def test_provision_prompt_uses_provided_type(self):
        from orb.interface.mcp.server.core import OpenResourceBrokerMCPServer

        server = OpenResourceBrokerMCPServer()
        prompt = server._generate_provision_prompt({"template_type": "spot_fleet"})
        assert "spot_fleet" in prompt


# ---------------------------------------------------------------------------
# sdk/client.py — no 'aws' sentinel in provider parameter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSDKClientNoAWSSentinel:
    def _source(self) -> str:
        return (SRC / "sdk" / "client.py").read_text()

    def test_no_aws_sentinel_comparison(self):
        source = self._source()
        assert '!= "aws"' not in source and "!= 'aws'" not in source, (
            "sdk/client.py must not use 'aws' as a sentinel value in provider comparison"
        )

    def test_provider_default_is_none(self):
        from orb.sdk.client import ORBClient

        sig = inspect.signature(ORBClient.__init__)
        provider_param = sig.parameters["provider"]
        assert provider_param.default is None, (
            "ORBClient.__init__ provider parameter must default to None, not 'aws'"
        )

    def test_explicit_provider_overrides_config(self):
        """Passing provider='mock' must override the config default."""
        from orb.sdk.client import ORBClient

        sdk = ORBClient(provider="mock", config={"provider": "aws"})
        assert sdk.provider == "mock"

    def test_no_provider_uses_config_default(self):
        """Not passing provider must leave config provider untouched."""
        from orb.sdk.client import ORBClient

        sdk = ORBClient(config={"provider": "aws"})
        assert sdk.provider == "aws"

    def test_none_provider_uses_config_default(self):
        """Explicitly passing provider=None must leave config provider untouched."""
        from orb.sdk.client import ORBClient

        sdk = ORBClient(provider=None, config={"provider": "aws"})
        assert sdk.provider == "aws"


# ---------------------------------------------------------------------------
# input_validator.py — validate_aws_region removed from generic layer
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateAWSRegionRemovedFromGenericLayer:
    def test_validate_aws_region_not_in_input_validator_class(self):
        from orb.infrastructure.validation.input_validator import InputValidator

        assert not hasattr(InputValidator, "validate_aws_region"), (
            "InputValidator must not have validate_aws_region — it belongs in providers/aws/"
        )

    def test_validate_aws_region_not_exported_from_validation_package(self):
        import orb.infrastructure.validation as pkg

        assert not hasattr(pkg, "validate_aws_region"), (
            "orb.infrastructure.validation must not export validate_aws_region"
        )

    def test_aws_region_regex_not_in_input_validator(self):
        from orb.infrastructure.validation.input_validator import InputValidator

        assert not hasattr(InputValidator, "AWS_REGION"), (
            "InputValidator must not contain AWS_REGION regex — it belongs in providers/aws/"
        )

    def test_validate_aws_region_not_in_init_all(self):
        import orb.infrastructure.validation as pkg

        all_exports = getattr(pkg, "__all__", [])
        assert "validate_aws_region" not in all_exports, (
            "validate_aws_region must not be in orb.infrastructure.validation.__all__"
        )


# ---------------------------------------------------------------------------
# providers/aws/validation/region_validator.py — validate_aws_region lives here
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSRegionValidatorInProvidersLayer:
    def test_module_exists(self):
        region_validator = SRC / "providers" / "aws" / "validation" / "region_validator.py"
        assert region_validator.exists(), (
            "src/orb/providers/aws/validation/region_validator.py must exist"
        )

    def test_validate_aws_region_importable(self):
        from orb.providers.aws.validation.region_validator import validate_aws_region

        assert callable(validate_aws_region)

    def test_valid_regions_accepted(self):
        from orb.providers.aws.validation.region_validator import validate_aws_region

        for region in ["us-east-1", "eu-west-2", "ap-south-1"]:
            assert validate_aws_region(region) == region

    def test_invalid_regions_rejected(self):
        from orb.infrastructure.validation import ValidationError
        from orb.providers.aws.validation.region_validator import validate_aws_region

        for region in ["us-east", "invalid", "us_east_1", "US-EAST-1"]:
            with pytest.raises(ValidationError):
                validate_aws_region(region)
