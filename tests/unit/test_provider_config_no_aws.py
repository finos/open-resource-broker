"""TDD test: provider_config_handler must not contain AWS-specific branching."""

from pathlib import Path

SOURCE = Path(__file__).parents[2] / "src/orb/interface/provider_config_handler.py"


def _source_text() -> str:
    return SOURCE.read_text()


def test_no_aws_import_in_provider_config_handler():
    """No direct import from orb.providers.aws in provider_config_handler."""
    source = _source_text()
    assert "from orb.providers.aws" not in source, (
        "provider_config_handler.py must not import from orb.providers.aws"
    )


def test_no_provider_type_aws_branching():
    """No hard-coded 'provider_type == \"aws\"' branching in provider_config_handler."""
    source = _source_text()
    assert 'provider_type == "aws"' not in source, (
        'provider_config_handler.py must not contain provider_type == "aws" branching'
    )
