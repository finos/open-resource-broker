"""Verify machine_command_handlers has no AWS-specific imports."""

import ast
import pathlib


def _get_imports(filepath: pathlib.Path) -> set[str]:
    tree = ast.parse(filepath.read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_no_aws_provider_imports():
    """machine_command_handlers must not import from orb.providers.aws."""
    imports = _get_imports(pathlib.Path("src/orb/interface/machine_command_handlers.py"))
    aws_imports = [m for m in imports if "providers.aws" in m]
    assert not aws_imports, f"AWS imports found: {aws_imports}"


def test_operation_type_has_start_stop():
    """ProviderOperationType must have START_INSTANCES and STOP_INSTANCES."""
    from orb.providers.base.strategy.provider_strategy import ProviderOperationType

    assert hasattr(ProviderOperationType, "START_INSTANCES"), "Missing START_INSTANCES"
    assert hasattr(ProviderOperationType, "STOP_INSTANCES"), "Missing STOP_INSTANCES"
