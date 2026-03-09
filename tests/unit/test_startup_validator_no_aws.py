"""Assert that startup_validator.py has no direct AWS provider imports."""

import ast
from pathlib import Path

VALIDATOR_PATH = (
    Path(__file__).parent.parent.parent
    / "src/orb/infrastructure/validation/startup_validator.py"
)


def _collect_imports(source: str) -> list[str]:
    """Return all module names imported in source (from X import Y → X)."""
    tree = ast.parse(source)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
    return modules


def test_no_aws_provider_imports_in_startup_validator() -> None:
    """startup_validator.py must not import from orb.providers.aws."""
    source = VALIDATOR_PATH.read_text()
    modules = _collect_imports(source)
    aws_imports = [m for m in modules if m.startswith("orb.providers.aws")]
    assert aws_imports == [], (
        f"startup_validator.py contains direct AWS provider imports: {aws_imports}. "
        "Delegate to provider strategy instead."
    )


def test_no_botocore_imports_in_startup_validator() -> None:
    """startup_validator.py must not import from botocore."""
    source = VALIDATOR_PATH.read_text()
    modules = _collect_imports(source)
    botocore_imports = [m for m in modules if m.startswith("botocore")]
    assert botocore_imports == [], (
        f"startup_validator.py contains botocore imports: {botocore_imports}. "
        "Use generic exception handling instead."
    )


def test_no_hardcoded_aws_string_in_startup_validator() -> None:
    """startup_validator.py must not contain hardcoded 'aws' provider type strings."""
    source = VALIDATOR_PATH.read_text()
    tree = ast.parse(source)
    aws_literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.lower() == "aws":
                aws_literals.append(node.value)
    assert aws_literals == [], (
        f"startup_validator.py contains hardcoded 'aws' string literals: {aws_literals}. "
        "Iterate all configured providers generically."
    )
