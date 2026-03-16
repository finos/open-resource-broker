"""TDD test: config_validator must not hard-import from providers/aws/."""

from pathlib import Path

VALIDATOR = Path(__file__).parents[3] / "src/orb/providers/config_validator.py"


def test_no_aws_import_in_config_validator():
    """No module-level import from orb.providers.aws in config_validator.py."""
    source = VALIDATOR.read_text()
    assert "from orb.providers.aws" not in source, (
        "config_validator.py must not import from orb.providers.aws at module level"
    )


def test_config_builder_importable_from_providers():
    """ProviderConfigBuilder is importable from orb.providers.config_builder."""
    from orb.providers.config_builder import ProviderConfigBuilder  # noqa: F401

    assert ProviderConfigBuilder is not None
