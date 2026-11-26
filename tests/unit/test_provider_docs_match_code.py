"""Provider documentation must agree with the code it describes."""

from pathlib import Path

import pytest

PROVIDER_DOCS = Path(__file__).resolve().parents[2] / "docs" / "root" / "providers"


def _read(relative: str) -> str:
    return (PROVIDER_DOCS / relative).read_text(encoding="utf-8")


def _default_scopes_table(text: str) -> str:
    """The text of the GCP scope table, from its header to the next heading."""
    start = text.index("| Default scope |")
    end = text.index("\n#", start)
    return text[start:end]


def test_gcp_doc_lists_every_default_scope():
    constants = pytest.importorskip("orb.providers.gcp.constants")
    table = _default_scopes_table(_read("gcp/configuration.md"))
    for scope in constants.DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES:
        assert f"`{scope}`" in table


def test_gcp_doc_does_not_list_compute_scope_as_default():
    constants = pytest.importorskip("orb.providers.gcp.constants")
    compute = "https://www.googleapis.com/auth/compute"
    assert compute not in constants.DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES
    assert f"`{compute}`" not in _default_scopes_table(_read("gcp/configuration.md"))


def test_azure_doc_names_every_supported_operation():
    module = pytest.importorskip("orb.providers.azure.strategy.azure_provider_strategy")
    # get_capabilities does not read instance state, so no strategy is built.
    strategy = module.AzureProviderStrategy.__new__(module.AzureProviderStrategy)
    operations = strategy.get_capabilities().supported_operations
    assert operations
    handlers = _read("azure/handlers.md")
    configuration = _read("azure/configuration.md")
    for operation in operations:
        assert f"`{operation.name}`" in handlers
        assert f"`{operation.name}`" in configuration
