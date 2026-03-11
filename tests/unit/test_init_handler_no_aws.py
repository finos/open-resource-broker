"""Verify init_command_handler has no AWS-specific code."""

import ast
import pathlib


def _get_source():
    return pathlib.Path("src/orb/interface/init_command_handler.py").read_text()


def _get_imports(filepath: pathlib.Path) -> set[str]:
    tree = ast.parse(filepath.read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_no_aws_provider_imports():
    """init_command_handler must not import from orb.providers.aws."""
    imports = _get_imports(pathlib.Path("src/orb/interface/init_command_handler.py"))
    aws_imports = [m for m in imports if "providers.aws" in m]
    assert not aws_imports, f"AWS imports found: {aws_imports}"


def test_no_common_aws_regions_constant():
    """_COMMON_AWS_REGIONS must not exist in init_command_handler."""
    source = _get_source()
    assert "_COMMON_AWS_REGIONS" not in source, "_COMMON_AWS_REGIONS still in init_command_handler"


def test_no_provider_type_aws_branching():
    """No 'if provider_type == \"aws\"' branching."""
    source = _get_source()
    assert 'provider_type == "aws"' not in source, "AWS provider branching still present"
    assert "provider_type == 'aws'" not in source, "AWS provider branching still present"


def test_no_us_east_1_literal():
    """No hardcoded 'us-east-1' string literals in init_command_handler."""
    source = _get_source()
    assert "us-east-1" not in source, "Hardcoded 'us-east-1' still present in init_command_handler"


def test_no_hardcoded_aws_fallback_provider():
    """No hardcoded 'aws' string used as a fallback provider value."""
    source = _get_source()
    # These are the patterns that were hardcoded fallbacks
    assert 'else "aws"' not in source, "Hardcoded 'aws' fallback still present"
    assert "else 'aws'" not in source, "Hardcoded 'aws' fallback still present"
    assert ': "aws"' not in source, "Hardcoded 'aws' default value still present"
    assert "= 'aws'" not in source, "Hardcoded 'aws' assignment still present"


def test_no_scheduler_metadata_hardcoded():
    """No hardcoded scheduler display metadata (if/elif blocks) in init_command_handler."""
    source = _get_source()
    assert '"Standalone usage"' not in source, "Hardcoded scheduler description still present"
    assert '"IBM Spectrum Symphony integration"' not in source, (
        "Hardcoded scheduler description still present"
    )


def test_no_hostfactory_config_root_hardcoding():
    """No hardcoded hostfactory config_root injection in init_command_handler."""
    source = _get_source()
    assert 'scheduler_type"] == "hostfactory"' not in source, (
        "Hardcoded hostfactory branching still present"
    )


def test_provider_strategy_has_get_available_regions():
    """Base ProviderStrategy must have get_available_regions method."""
    from orb.providers.base.strategy.provider_strategy import ProviderStrategy

    assert hasattr(ProviderStrategy, "get_available_regions"), (
        "get_available_regions not on ProviderStrategy"
    )


def test_provider_strategy_has_get_default_region():
    """Base ProviderStrategy must have get_default_region method."""
    from orb.providers.base.strategy.provider_strategy import ProviderStrategy

    assert hasattr(ProviderStrategy, "get_default_region"), (
        "get_default_region not on ProviderStrategy"
    )


def test_provider_strategy_has_get_cli_extra_config_keys():
    """Base ProviderStrategy must have get_cli_extra_config_keys method."""
    from orb.providers.base.strategy.provider_strategy import ProviderStrategy

    assert hasattr(ProviderStrategy, "get_cli_extra_config_keys"), (
        "get_cli_extra_config_keys not on ProviderStrategy"
    )


def test_provider_strategy_has_get_cli_infrastructure_defaults():
    """Base ProviderStrategy must have get_cli_infrastructure_defaults method."""
    from orb.providers.base.strategy.provider_strategy import ProviderStrategy

    assert hasattr(ProviderStrategy, "get_cli_infrastructure_defaults"), (
        "get_cli_infrastructure_defaults not on ProviderStrategy"
    )


def test_provider_strategy_base_defaults():
    """Base ProviderStrategy default implementations return empty/no-op values."""
    from unittest.mock import MagicMock

    from orb.providers.base.strategy.provider_strategy import ProviderStrategy

    # Create a minimal concrete subclass to test the base defaults
    class _ConcreteStrategy(ProviderStrategy):
        @property
        def provider_type(self) -> str:
            return "test"

        def initialize(self) -> bool:
            return True

        async def execute_operation(self, operation):
            pass

        def get_capabilities(self):
            pass

        def check_health(self):
            pass

        def generate_provider_name(self, config):
            return "test"

        def parse_provider_name(self, name):
            return {}

        def get_provider_name_pattern(self) -> str:
            return "{type}"

        def cleanup(self) -> None:
            pass

    config = MagicMock()
    strategy = _ConcreteStrategy(config)

    assert strategy.get_default_region() == ""
    assert strategy.get_cli_extra_config_keys() == set()
    assert strategy.get_cli_infrastructure_defaults(MagicMock()) == {}


def _make_aws_strategy():
    """Create a minimal AWSProviderStrategy for unit testing."""
    from unittest.mock import MagicMock

    from orb.providers.aws.configuration.config import AWSProviderConfig
    from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

    config = AWSProviderConfig(region="us-east-1")  # type: ignore[call-arg]
    logger = MagicMock()
    return AWSProviderStrategy(config=config, logger=logger)


def test_aws_provider_strategy_overrides_get_default_region():
    """AWSProviderStrategy.get_default_region() returns 'us-east-1'."""
    strategy = _make_aws_strategy()
    assert strategy.get_default_region() == "us-east-1"


def test_aws_provider_strategy_overrides_get_cli_extra_config_keys():
    """AWSProviderStrategy.get_cli_extra_config_keys() returns {'fleet_role'}."""
    strategy = _make_aws_strategy()
    assert strategy.get_cli_extra_config_keys() == {"fleet_role"}


def test_aws_provider_strategy_get_cli_infrastructure_defaults_subnet_ids():
    """AWSProviderStrategy.get_cli_infrastructure_defaults extracts subnet_ids."""
    from unittest.mock import MagicMock

    strategy = _make_aws_strategy()
    args = MagicMock()
    args.subnet_ids = "subnet-aaa, subnet-bbb"
    args.security_group_ids = None
    args.fleet_role = None

    result = strategy.get_cli_infrastructure_defaults(args)
    assert result == {"subnet_ids": ["subnet-aaa", "subnet-bbb"]}


def test_aws_provider_strategy_get_cli_infrastructure_defaults_all_fields():
    """AWSProviderStrategy.get_cli_infrastructure_defaults extracts all AWS fields."""
    from unittest.mock import MagicMock

    strategy = _make_aws_strategy()
    args = MagicMock()
    args.subnet_ids = "subnet-aaa,subnet-bbb"
    args.security_group_ids = "sg-111,sg-222"
    args.fleet_role = "arn:aws:iam::123:role/FleetRole"

    result = strategy.get_cli_infrastructure_defaults(args)
    assert result["subnet_ids"] == ["subnet-aaa", "subnet-bbb"]
    assert result["security_group_ids"] == ["sg-111", "sg-222"]
    assert result["fleet_role"] == "arn:aws:iam::123:role/FleetRole"


def test_scheduler_registry_get_display_metadata_known_type():
    """SchedulerRegistry.get_display_metadata returns correct metadata for known types."""
    from typing import cast

    from orb.infrastructure.scheduler.registry import SchedulerRegistry

    registry = cast(SchedulerRegistry, SchedulerRegistry())
    meta = registry.get_display_metadata("default")
    assert meta["display_name"] == "default"
    assert meta["description"] == "Standalone usage"


def test_scheduler_registry_get_display_metadata_hostfactory():
    """SchedulerRegistry.get_display_metadata returns hostfactory metadata."""
    from typing import cast

    from orb.infrastructure.scheduler.registry import SchedulerRegistry

    registry = cast(SchedulerRegistry, SchedulerRegistry())
    meta = registry.get_display_metadata("hostfactory")
    assert meta["display_name"] == "hostfactory"
    assert "Symphony" in meta["description"]


def test_scheduler_registry_get_display_metadata_unknown_type():
    """SchedulerRegistry.get_display_metadata falls back gracefully for unknown types."""
    from typing import cast

    from orb.infrastructure.scheduler.registry import SchedulerRegistry

    registry = cast(SchedulerRegistry, SchedulerRegistry())
    meta = registry.get_display_metadata("unknown_scheduler")
    assert meta["display_name"] == "unknown_scheduler"


def test_scheduler_registry_get_extra_config_for_hostfactory():
    """SchedulerRegistry.get_extra_config_for_type returns config_root for hostfactory."""
    from typing import cast

    from orb.infrastructure.scheduler.registry import SchedulerRegistry

    registry = cast(SchedulerRegistry, SchedulerRegistry())
    extra = registry.get_extra_config_for_type("hostfactory")
    assert extra == {"config_root": "$ORB_CONFIG_DIR"}


def test_scheduler_registry_get_extra_config_for_default():
    """SchedulerRegistry.get_extra_config_for_type returns empty dict for default."""
    from typing import cast

    from orb.infrastructure.scheduler.registry import SchedulerRegistry

    registry = cast(SchedulerRegistry, SchedulerRegistry())
    extra = registry.get_extra_config_for_type("default")
    assert extra == {}


def test_interactive_setup_raises_when_no_providers_registered():
    """_interactive_setup raises ValueError when provider registry returns no providers."""
    from unittest.mock import patch

    from orb.interface.init_command_handler import _interactive_setup

    with (
        patch(
            "orb.interface.init_command_handler._get_available_schedulers",
            return_value=[
                {"type": "default", "display_name": "default", "description": "Standalone usage"}
            ],
        ),
        patch(
            "orb.interface.init_command_handler._get_available_providers",
            return_value=[],
        ),
        patch("builtins.input", return_value="1"),
    ):
        try:
            _interactive_setup()
            assert False, "Expected ValueError was not raised"
        except ValueError as exc:
            assert "No providers registered" in str(exc)


def test_get_default_config_raises_when_no_providers_and_no_provider_arg():
    """_get_default_config raises ValueError when registry is empty and args.provider is None."""
    from unittest.mock import MagicMock, patch

    from orb.interface.init_command_handler import _get_default_config

    args = MagicMock()
    args.provider = None

    with patch(
        "orb.interface.init_command_handler._get_available_providers",
        return_value=[],
    ):
        try:
            _get_default_config(args)
            assert False, "Expected ValueError was not raised"
        except ValueError as exc:
            assert "No providers registered" in str(exc)


def test_get_default_scheduler_type_empty_registry_returns_default():
    """_get_default_scheduler_type returns 'default' when registry has no types."""
    from unittest.mock import MagicMock, patch

    from orb.interface.init_command_handler import _get_default_scheduler_type

    mock_registry = MagicMock()
    mock_registry.get_available_types_with_registration.return_value = []

    with patch(
        "orb.infrastructure.scheduler.registry.get_scheduler_registry",
        return_value=mock_registry,
    ):
        result = _get_default_scheduler_type()

    assert result == "default"


def test_get_default_scheduler_type_returns_first_registered_type():
    """_get_default_scheduler_type returns the first type from the registry."""
    from unittest.mock import MagicMock, patch

    from orb.interface.init_command_handler import _get_default_scheduler_type

    mock_registry = MagicMock()
    mock_registry.get_available_types_with_registration.return_value = ["default", "hostfactory"]

    with patch(
        "orb.infrastructure.scheduler.registry.get_scheduler_registry",
        return_value=mock_registry,
    ):
        result = _get_default_scheduler_type()

    assert result == "default"


def test_get_default_scheduler_type_exception_returns_default():
    """_get_default_scheduler_type returns 'default' when registry raises."""
    from unittest.mock import patch

    from orb.interface.init_command_handler import _get_default_scheduler_type

    with patch(
        "orb.infrastructure.scheduler.registry.get_scheduler_registry",
        side_effect=RuntimeError("registry unavailable"),
    ):
        result = _get_default_scheduler_type()

    assert result == "default"


def test_get_available_schedulers_no_duplicates():
    """_get_available_schedulers deduplicates entries with the same display_name."""
    from unittest.mock import MagicMock, patch

    from orb.interface.init_command_handler import _get_available_schedulers

    mock_registry = MagicMock()
    mock_registry.get_available_types_with_registration.return_value = [
        "hostfactory",
        "hf",
        "default",
    ]
    mock_registry.get_display_metadata.side_effect = lambda t: {
        "hostfactory": {"display_name": "hostfactory", "description": "Symphony"},
        "hf": {"display_name": "hostfactory", "description": "Symphony"},
        "default": {"display_name": "default", "description": "Standalone usage"},
    }[t]

    with patch(
        "orb.infrastructure.scheduler.registry.get_scheduler_registry",
        return_value=mock_registry,
    ):
        result = _get_available_schedulers()

    display_names = [s["display_name"] for s in result]
    assert display_names.count("hostfactory") == 1
    assert display_names.count("default") == 1
    assert len(result) == 2
