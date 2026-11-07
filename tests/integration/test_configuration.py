"""Default configuration file and AWS launch template configuration."""

import json
from pathlib import Path

from orb.providers.aws.configuration.config import AWSProviderConfig, LaunchTemplateConfiguration

_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "default_config.json"


def _load_default_config() -> dict:
    with _DEFAULT_CONFIG.open() as f:
        return json.load(f)


def test_default_config_validation():
    """The shipped default config is valid JSON with a provider section."""
    config = _load_default_config()

    assert "provider" in config
    assert isinstance(config["provider"]["providers"], list)
    assert isinstance(config["provider"]["provider_defaults"], dict)


def test_aws_provider_config_class():
    """LaunchTemplateConfiguration defaults are carried by AWSProviderConfig."""
    lt_config = LaunchTemplateConfiguration()  # type: ignore[call-arg]

    assert lt_config.create_per_request is True
    assert lt_config.naming_strategy == "request_based"
    assert lt_config.max_versions_per_template == 10

    aws_config = AWSProviderConfig(profile="default")  # type: ignore[call-arg]

    assert isinstance(aws_config.launch_template, LaunchTemplateConfiguration)


def test_configuration_loading():
    """The default config's AWS provider entry builds an AWSProviderConfig."""
    config = _load_default_config()
    aws_entries = [p for p in config["provider"]["providers"] if p["type"] == "aws"]
    assert aws_entries, "default config declares no aws provider"

    aws_config = AWSProviderConfig(
        **{"region": "us-east-1", "profile": "default", **aws_entries[0]["config"]}
    )

    assert aws_config.region == "us-east-1"
    assert isinstance(aws_config.launch_template, LaunchTemplateConfiguration)
    assert aws_config.launch_template.create_per_request is True
