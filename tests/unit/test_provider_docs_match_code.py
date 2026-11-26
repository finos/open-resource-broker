"""Provider documentation must agree with the code it describes."""

from pathlib import Path

from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy
from orb.providers.gcp.constants import DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES

PROVIDER_DOCS = Path(__file__).resolve().parents[2] / "docs" / "root" / "providers"


def _read(relative: str) -> str:
    return (PROVIDER_DOCS / relative).read_text(encoding="utf-8")


def _default_scopes_table(text: str) -> str:
    """The text of the GCP scope table, from its header to the next heading."""
    start = text.index("| Default scope |")
    end = text.index("\n#", start)
    return text[start:end]


def test_gcp_doc_lists_every_default_scope():
    table = _default_scopes_table(_read("gcp/configuration.md"))
    for scope in DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES:
        assert f"`{scope}`" in table


def test_gcp_doc_does_not_list_compute_scope_as_default():
    compute = "https://www.googleapis.com/auth/compute"
    assert compute not in DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES
    assert compute not in _default_scopes_table(_read("gcp/configuration.md"))


def test_azure_doc_names_every_supported_operation():
    # get_capabilities does not read instance state, so no strategy is built.
    strategy = AzureProviderStrategy.__new__(AzureProviderStrategy)
    operations = strategy.get_capabilities().supported_operations
    assert operations
    handlers = _read("azure/handlers.md")
    configuration = _read("azure/configuration.md")
    for operation in operations:
        assert f"`{operation.name}`" in handlers
        assert f"`{operation.name}`" in configuration
