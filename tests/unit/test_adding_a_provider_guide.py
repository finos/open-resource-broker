"""The provider authoring guide must reference code that exists."""

import re
from pathlib import Path

from orb.infrastructure.scheduler.hostfactory.field_mapping_port import FieldMappingPort
from orb.providers.base.provider_cli_spec_port import ProviderCLISpecPort
from orb.providers.base.provider_plugin import ProviderPlugin

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE = REPO_ROOT / "docs" / "root" / "developer_guide" / "adding_a_provider.md"


def _text() -> str:
    return GUIDE.read_text(encoding="utf-8")


def test_cited_source_files_exist():
    cited = set(re.findall(r"`(src/orb/[\w./-]+\.py)`", _text()))
    assert cited
    for rel in sorted(cited):
        assert (REPO_ROOT / rel).is_file(), rel


def test_plugin_hooks_named_in_guide_exist():
    text = _text()
    for hook in (
        "strategy_factory",
        "config_factory",
        "template_dto_config",
        "cli_spec",
        "field_mapping",
        "defaults_loader",
        "template_example_generator",
        "resolver_factory",
        "validator_factory",
        "strategy_class",
        "default_api",
        "template_row_migration",
        "display_name",
        "provider_settings_class",
        "template_class",
        "register_auth_strategies",
        "register_additional_services",
        "_do_initialize",
    ):
        assert hook in text, hook
        assert hasattr(ProviderPlugin, hook), hook


def test_protocol_methods_in_guide_match_protocols():
    text = _text()
    for protocol in (FieldMappingPort, ProviderCLISpecPort):
        methods = [
            n for n in vars(protocol) if not n.startswith("_") and callable(vars(protocol)[n])
        ]
        assert methods
        for method in methods:
            assert method in text, f"{protocol.__name__}.{method}"
