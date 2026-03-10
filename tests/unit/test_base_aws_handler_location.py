"""
AST-based test asserting BaseAWSHandler lives in providers/aws/, not application/base/.
"""

import ast
import pathlib

ROOT = pathlib.Path(__file__).parents[2] / "src" / "orb"

APP_MODULE = ROOT / "application" / "base" / "provider_handlers.py"
AWS_MODULE = ROOT / "providers" / "aws" / "infrastructure" / "handlers" / "base_handler.py"


def _class_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def test_base_aws_handler_absent_from_application_layer():
    """BaseAWSHandler must NOT be defined in the application layer."""
    assert "BaseAWSHandler" not in _class_names(APP_MODULE), (
        "BaseAWSHandler is still defined in application/base/provider_handlers.py — "
        "it must be moved to providers/aws/"
    )


def test_base_aws_handler_present_in_aws_provider():
    """BaseAWSHandler must be defined in providers/aws/infrastructure/handlers/base_handler.py."""
    assert "BaseAWSHandler" in _class_names(AWS_MODULE), (
        "BaseAWSHandler is not defined in providers/aws/infrastructure/handlers/base_handler.py"
    )


def test_base_aws_handler_exported_from_handlers_init():
    """BaseAWSHandler must be exported from the handlers __init__.py."""
    init_path = ROOT / "providers" / "aws" / "infrastructure" / "handlers" / "__init__.py"
    source = init_path.read_text()
    assert "BaseAWSHandler" in source, (
        "BaseAWSHandler is not exported from providers/aws/infrastructure/handlers/__init__.py"
    )
