"""provider_config_mapping normalises registry config input."""

from orb.config.schemas.provider_strategy_schema import ProviderInstanceConfig
from orb.providers.base.config_input import provider_config_mapping


def test_mapping_drops_discriminators_and_returns_new_dict() -> None:
    original = {"region": "us-east-1", "type": "aws", "provider_type": "aws"}

    result = provider_config_mapping(original)

    assert result == {"region": "us-east-1"}
    assert result is not original
    assert original["type"] == "aws"


def test_provider_instance_config_is_unwrapped() -> None:
    instance = ProviderInstanceConfig.model_validate(
        {"name": "a-1", "type": "aws", "config": {"region": "eu-west-1"}}
    )

    assert provider_config_mapping(instance) == {"region": "eu-west-1"}


def test_none_gives_empty_dict() -> None:
    assert provider_config_mapping(None) == {}


def test_non_mapping_model_passes_through_unchanged() -> None:
    from pydantic import BaseModel

    class Settings(BaseModel):
        region: str = "us-east-1"

    model = Settings()

    assert provider_config_mapping(model) is model
    assert provider_config_mapping(42) == 42
