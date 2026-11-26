"""Provider documentation must agree with the code it describes.

The code is read with ``ast`` rather than imported, so these tests run in every
environment, including those without the provider extras installed.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROVIDER_DOCS = REPO_ROOT / "docs" / "root" / "providers"
PROVIDER_SRC = REPO_ROOT / "src" / "orb" / "providers"


def _read(relative: str) -> str:
    return (PROVIDER_DOCS / relative).read_text(encoding="utf-8")


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _default_gcp_scopes() -> tuple[str, ...]:
    for node in _parse(PROVIDER_SRC / "gcp" / "constants.py").body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES"
            and node.value is not None
        ):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("DEFAULT_GCP_SERVICE_ACCOUNT_SCOPES not found")


def _azure_supported_operations() -> set[str]:
    path = PROVIDER_SRC / "azure" / "strategy" / "azure_provider_strategy.py"
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.FunctionDef) and node.name == "get_capabilities":
            return {
                sub.attr
                for sub in ast.walk(node)
                if isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "ProviderOperationType"
            }
    raise AssertionError("get_capabilities not found")


def _default_scopes_table(text: str) -> str:
    """The text of the GCP scope table, from its header to the next heading."""
    start = text.index("| Default scope |")
    end = text.index("\n#", start)
    return text[start:end]


def test_gcp_doc_lists_every_default_scope():
    scopes = _default_gcp_scopes()
    assert scopes
    table = _default_scopes_table(_read("gcp/configuration.md"))
    for scope in scopes:
        assert f"`{scope}`" in table


def test_gcp_doc_does_not_list_compute_scope_as_default():
    scopes = _default_gcp_scopes()
    assert scopes
    compute = "https://www.googleapis.com/auth/compute"
    assert compute not in scopes
    assert f"`{compute}`" not in _default_scopes_table(_read("gcp/configuration.md"))


def test_azure_doc_names_every_supported_operation():
    operations = _azure_supported_operations()
    assert operations
    handlers = _read("azure/handlers.md")
    configuration = _read("azure/configuration.md")
    for operation in sorted(operations):
        assert f"`{operation}`" in handlers
        assert f"`{operation}`" in configuration
