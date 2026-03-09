"""Assert that startup_validator.py has no direct AWS provider imports."""

import ast
from pathlib import Path
from unittest.mock import MagicMock

from orb.config.schemas.app_schema import AppConfig
from orb.config.schemas.provider_strategy_schema import ProviderConfig, ProviderInstanceConfig
from orb.infrastructure.validation.startup_validator import StartupValidator

VALIDATOR_PATH = (
    Path(__file__).parent.parent.parent / "src/orb/infrastructure/validation/startup_validator.py"
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


def _make_app_config_with_providers() -> AppConfig:
    """Build a minimal AppConfig with one provider instance."""
    provider = ProviderInstanceConfig(name="test-provider", type="testprovider")  # type: ignore[call-arg]
    return AppConfig(provider=ProviderConfig(providers=[provider]))  # type: ignore[call-arg]


def test_credentials_checker_called_with_provider_list() -> None:
    """credentials_checker callable receives the provider list from app_config."""
    checker = MagicMock(return_value=True)
    validator = StartupValidator(credentials_checker=checker)
    validator.app_config = _make_app_config_with_providers()

    result = validator._check_provider_credentials()

    assert result is True
    checker.assert_called_once_with(validator.app_config.provider.providers)


def test_credentials_checker_none_skips_validation() -> None:
    """When credentials_checker is None, _check_provider_credentials returns True."""
    validator = StartupValidator(credentials_checker=None)
    validator.app_config = _make_app_config_with_providers()

    result = validator._check_provider_credentials()

    assert result is True


def test_credentials_checker_returns_false_triggers_warning() -> None:
    """When credentials_checker returns False, _check_provider_credentials returns False."""
    checker = MagicMock(return_value=False)
    validator = StartupValidator(credentials_checker=checker)
    validator.app_config = _make_app_config_with_providers()

    result = validator._check_provider_credentials()

    assert result is False
