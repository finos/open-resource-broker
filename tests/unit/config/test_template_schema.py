"""Unit tests for TemplateConfig schema — tasks 1944 and 1945."""

from orb.config.schemas.template_schema import TemplateConfig

# --- Task 1944: default_provider_api defaults to EC2Fleet ---


def test_default_provider_api_defaults_to_none():
    cfg = TemplateConfig()
    assert cfg.default_provider_api is None


def test_default_provider_api_accepts_explicit_value():
    cfg = TemplateConfig(default_provider_api="EC2Fleet")
    assert cfg.default_provider_api == "EC2Fleet"


# --- Task 1945: filename_patterns field ---


def test_filename_patterns_defaults():
    cfg = TemplateConfig()
    assert cfg.filename_patterns.provider_specific == "{provider_name}_templates.json"
    assert cfg.filename_patterns.provider_type == "{provider_type}_templates.json"
    assert cfg.filename_patterns.generic == "templates.json"


def test_filename_patterns_override():
    cfg = TemplateConfig(filename_patterns={"provider_type": "custom_{provider_type}.json"})
    assert cfg.filename_patterns.provider_type == "custom_{provider_type}.json"
    assert cfg.filename_patterns.generic == "templates.json"
