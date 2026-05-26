"""OCI provider registration."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort
    from orb.providers.registry import ProviderRegistry


def _extract_oci_provider_settings(provider_config: Any) -> dict[str, Any]:
    """Unwrap provider instance entries to the inner OCI settings dict."""
    if isinstance(provider_config, dict):
        nested = provider_config.get("config")
        if isinstance(nested, dict):
            return nested
        return provider_config
    if hasattr(provider_config, "config"):
        nested = provider_config.config
        if isinstance(nested, dict):
            return nested
        return nested or {}
    return provider_config or {}


def create_oci_strategy(provider_config: Any) -> Any:
    """Create OCI provider strategy from configuration."""
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.oci.configuration.config import OCIProviderConfig
    from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy

    try:
        if isinstance(provider_config, OCIProviderConfig):
            oci_config = provider_config
        else:
            oci_config = OCIProviderConfig(**_extract_oci_provider_settings(provider_config))

        logger = LoggingAdapter()
        strategy = OCIProviderStrategy(config=oci_config, logger=logger)

        if not strategy.initialize():
            raise RuntimeError("Failed to initialize OCI provider strategy")

        return strategy
    except ImportError as exc:
        raise ImportError(f"OCI provider strategy not available: {exc!s}")
    except Exception as exc:
        raise RuntimeError(f"Failed to create OCI strategy: {exc!s}")


def create_oci_config(data: Any) -> Any:
    """Create OCI configuration from data dictionary."""
    from orb.providers.oci.configuration.config import OCIProviderConfig

    try:
        if isinstance(data, OCIProviderConfig):
            return data
        if isinstance(data, dict) or hasattr(data, "config"):
            return OCIProviderConfig(**_extract_oci_provider_settings(data))
        return OCIProviderConfig()
    except Exception as exc:
        raise RuntimeError(f"Failed to create OCI config: {exc!s}")


def create_oci_resolver() -> Any:
    """Create OCI template resolver."""
    return None


def create_oci_validator(provider_config: Any = None) -> Any:
    """Create OCI template validator."""
    _ = provider_config
    return None


def register_oci_provider_settings() -> None:
    """Register OCIProviderConfig with provider settings registry."""
    try:
        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
        from orb.providers.oci.configuration.config import OCIProviderConfig

        ProviderSettingsRegistry.register_provider_settings("oci", OCIProviderConfig)
    except ImportError:
        pass


def register_oci_provider(
    registry: "Optional[ProviderRegistry]" = None,
    logger: "Optional[LoggingPort]" = None,
    instance_name: Optional[str] = None,
) -> None:
    """Register OCI provider with the provider registry."""
    if registry is None:
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()

    from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy

    if instance_name:
        registry.register_provider_instance(
            provider_type="oci",
            instance_name=instance_name,
            strategy_factory=create_oci_strategy,
            config_factory=create_oci_config,
            resolver_factory=create_oci_resolver,
            validator_factory=create_oci_validator,
        )
    else:
        registry.register_provider(
            provider_type="oci",
            strategy_factory=create_oci_strategy,
            config_factory=create_oci_config,
            resolver_factory=create_oci_resolver,
            validator_factory=create_oci_validator,
            strategy_class=OCIProviderStrategy,
        )

    # Ensure base OCI provider components are available when type registration happens.
    initialize_oci_provider(logger=logger)

    if logger:
        logger.info("OCI provider registered successfully")


def register_oci_provider_instance(provider_instance: Any, logger: Optional[Any] = None) -> bool:
    """Register OCI provider instance with Provider Registry."""
    try:
        from orb.providers.registry import get_provider_registry
        from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy

        registry = get_provider_registry()

        if not registry.is_provider_registered("oci"):
            registry.register_provider(
                provider_type="oci",
                strategy_factory=create_oci_strategy,
                config_factory=create_oci_config,
                resolver_factory=create_oci_resolver,
                validator_factory=create_oci_validator,
                strategy_class=OCIProviderStrategy,
            )

        registry.register_provider_instance(
            provider_type="oci",
            instance_name=provider_instance.name,
            strategy_factory=create_oci_strategy,
            config_factory=create_oci_config,
            resolver_factory=create_oci_resolver,
            validator_factory=create_oci_validator,
        )
        return True
    except Exception as exc:
        if logger:
            logger.error(
                "Failed to register OCI provider instance '%s': %s", provider_instance.name, exc
            )
        return False


def initialize_oci_provider(logger: Optional["LoggingPort"] = None) -> None:
    """Initialize OCI provider components."""
    register_oci_provider_settings()
    try:
        from orb.domain.base.ports.provider_cli_spec_port import CLISpecRegistry
        from orb.providers.oci.cli.oci_cli_spec import OCICLISpec

        CLISpecRegistry.register("oci", OCICLISpec())
    except Exception as exc:
        if logger:
            logger.warning("Failed to register OCI CLI spec: %s", exc, exc_info=True)
    if logger:
        logger.info("OCI provider initialization completed successfully")


with suppress(Exception):
    register_oci_provider_settings()
