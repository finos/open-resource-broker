"""Kubernetes Provider Registration.

Mirrors :mod:`orb.providers.aws.registration` for the modern kubernetes
provider.  Hits every registry the AWS provider hits so the kubernetes
provider has parity with the AWS integration surface from day one:

* ``ProviderRegistry``           — strategy / config / resolver / validator factories
* ``ProviderSettingsRegistry``   — typed BaseSettings class for config-file parsing
* ``CLISpecRegistry``            — only when the CLI spec module is present
* ``FieldMappingRegistry``       — only when the HostFactory field-mapping module is present
* ``DefaultsLoaderRegistry``     — provider defaults loader
* ``AuthRegistry``               — provider-side auth strategies (placeholder in Phase A)
* ``TemplateExtensionRegistry``  — only when the AWS-style DTO config exists (Phase B+)
* ``TemplateAdapterPort``        — only when the AWS-style adapter exists (Phase B+)
* ``TemplateExampleGeneratorPort`` — only when the example generator exists (Phase G)

Registrations that depend on phase-B+ modules are wrapped in defensive
``ImportError`` guards so this module imports cleanly in Phase A.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.domain.base.ports import LoggingPort
    from orb.domain.template.factory import TemplateFactory
    from orb.providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Strategy / config / resolver / validator factories
# ---------------------------------------------------------------------------


def create_kubernetes_strategy(provider_config: Any) -> Any:
    """Create a :class:`KubernetesProviderStrategy` from configuration.

    Accepts a :class:`KubernetesProviderConfig`, a ``ProviderInstanceConfig``,
    or a raw config dict.  The DI container is consulted opportunistically
    for ``ConfigurationPort`` and ``ConsolePort`` — failures are logged at
    debug level and the strategy is constructed without them.
    """
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
    from orb.providers.kubernetes.strategy.kubernetes_provider_strategy import (
        KubernetesProviderStrategy,
    )

    try:
        if isinstance(provider_config, KubernetesProviderConfig):
            k8s_config = provider_config
            provider_instance_config = None
            provider_name = None
        elif hasattr(provider_config, "config"):
            config_data = provider_config.config
            provider_instance_config = provider_config
            provider_name = provider_config.name
            k8s_config = KubernetesProviderConfig(**config_data)
        else:
            provider_instance_config = None
            provider_name = None
            k8s_config = KubernetesProviderConfig(**(provider_config or {}))

        logger = LoggingAdapter()

        config_port = None
        try:
            from orb.domain.base.ports.configuration_port import ConfigurationPort
            from orb.infrastructure.di.container import get_container

            config_port = get_container().get(ConfigurationPort)
        except Exception as exc:
            logger.debug("Could not get config port from DI container: %s", exc)

        console_port = None
        try:
            from orb.domain.base.ports.console_port import ConsolePort
            from orb.infrastructure.di.container import get_container

            console_port = get_container().get(ConsolePort)
        except Exception as exc:
            logger.debug("Could not get console port from DI container: %s", exc)

        strategy = KubernetesProviderStrategy(
            config=k8s_config,
            logger=logger,
            provider_name=provider_name,
            provider_instance_config=provider_instance_config,
            config_port=config_port,
            console=console_port,
        )

        if not strategy.initialize():
            raise RuntimeError("Failed to initialize Kubernetes provider strategy")

        if provider_name:
            strategy._provider_name = provider_name  # type: ignore[assignment]

        return strategy

    except ImportError as exc:
        raise ImportError(f"Kubernetes provider strategy not available: {exc!s}")
    except Exception as exc:
        raise RuntimeError(f"Failed to create Kubernetes strategy: {exc!s}")


def create_kubernetes_config(data: dict[str, Any]) -> Any:
    """Create :class:`KubernetesProviderConfig` from a dict of values."""
    try:
        from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig

        return KubernetesProviderConfig(**data)
    except ImportError as exc:
        raise ImportError(f"Kubernetes configuration not available: {exc!s}")
    except Exception as exc:
        raise RuntimeError(f"Failed to create Kubernetes config: {exc!s}")


def create_kubernetes_resolver() -> Any:
    """Phase A: no provider-side template resolver is needed."""
    return None


def create_kubernetes_validator(provider_config: Any = None) -> Any:
    """Phase A: no provider-side template validator is shipped yet.

    Returns ``None`` so the provider registry falls back to the generic
    validation surface.  Phase B (Pod handler) introduces a concrete
    validator implementation in
    ``orb.providers.kubernetes.infrastructure.adapters``.
    """
    return None


# ---------------------------------------------------------------------------
# Auxiliary registrations
# ---------------------------------------------------------------------------


def register_kubernetes_provider_settings() -> None:
    """Register :class:`KubernetesProviderConfig` with the provider settings registry."""
    try:
        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
        from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig

        ProviderSettingsRegistry.register_provider_settings("kubernetes", KubernetesProviderConfig)
    except ImportError:
        # Settings registry not available — operator must not be running
        # the configuration loader path; nothing to do.
        pass
    except Exception as exc:
        raise RuntimeError(f"Failed to register Kubernetes provider settings: {exc!s}")


def register_kubernetes_extensions(logger: "Optional[LoggingPort]" = None) -> None:
    """Register template-DTO extensions with the global template extension registry.

    The kubernetes-specific DTO config class arrives in Phase B alongside the
    Pod handler.  Until that lands the registration is a documented no-op so
    this module imports cleanly and the rest of the provider scaffolding is
    exercised by tests.
    """
    try:
        from orb.infrastructure.registry.template_extension_registry import (
            TemplateExtensionRegistry,
        )
        from orb.providers.kubernetes.domain.template.kubernetes_template_dto_config import (  # type: ignore[import-not-found]  # noqa: PLC0415
            KubernetesTemplateDTOConfig,
        )

        TemplateExtensionRegistry.register_extension("kubernetes", KubernetesTemplateDTOConfig)
        if logger:
            logger.debug("Kubernetes template extensions registered successfully")
    except ImportError:
        # Phase A: DTO config not present yet — log only at debug level.
        if logger:
            logger.debug(
                "Kubernetes template DTO config not present yet "
                "(introduced in Phase B alongside the Pod handler)."
            )
    except Exception as exc:
        if logger:
            logger.error(
                "Failed to register Kubernetes template extensions: %s", exc, exc_info=True
            )
        raise


def register_kubernetes_auth_strategies(logger: "Optional[LoggingPort]" = None) -> None:
    """Register Kubernetes auth strategies with the auth registry.

    Phase A: the kubernetes provider does not yet ship an inbound HTTP auth
    strategy.  The kube-API auth helpers
    (:mod:`orb.providers.kubernetes.auth.in_cluster` and
    :mod:`orb.providers.kubernetes.auth.kubeconfig`) are separate concerns —
    they bootstrap the kubernetes API client, not the ORB REST surface.
    This function is wired into ``initialize_kubernetes_provider`` so the
    integration point exists; concrete strategies arrive when needed.
    """
    if logger:
        logger.debug(
            "Kubernetes provider has no inbound HTTP auth strategies in Phase A; "
            "kube-API auth is handled via providers.kubernetes.auth.*"
        )


def register_kubernetes_template_factory(
    factory: "TemplateFactory", logger: "Optional[LoggingPort]" = None
) -> None:
    """Register the Kubernetes template class with the template factory.

    The concrete template aggregate ships in Phase B; until then this is
    a defensive no-op that logs at debug level.
    """
    try:
        from orb.providers.kubernetes.domain.template.kubernetes_template_aggregate import (  # type: ignore[import-not-found]  # noqa: PLC0415
            KubernetesTemplate,
        )

        factory.register_provider_template_class("kubernetes", KubernetesTemplate)
        if logger:
            logger.info("Kubernetes template class registered with factory")
    except ImportError:
        if logger:
            logger.debug(
                "Kubernetes template class not yet available "
                "(introduced in Phase B alongside the Pod handler)."
            )
    except Exception as exc:
        if logger:
            logger.warning("Failed to register Kubernetes template factory: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Provider-registry entry point
# ---------------------------------------------------------------------------


def register_kubernetes_provider(
    registry: "Optional[ProviderRegistry]" = None,
    logger: "Optional[LoggingPort]" = None,
    instance_name: Optional[str] = None,
) -> None:
    """Register the Kubernetes provider with the provider registry.

    Args:
        registry: Provider registry instance (optional — fetched from the
            global registry singleton when omitted).
        logger: Logger port for logging (optional).
        instance_name: Optional instance name for multi-instance support.
    """
    if registry is None:
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()

    try:
        from orb.providers.kubernetes.strategy.kubernetes_provider_strategy import (
            KubernetesProviderStrategy,
        )

        if instance_name:
            registry.register_provider_instance(
                provider_type="kubernetes",
                instance_name=instance_name,
                strategy_factory=create_kubernetes_strategy,
                config_factory=create_kubernetes_config,
                resolver_factory=create_kubernetes_resolver,
                validator_factory=create_kubernetes_validator,
            )
        else:
            registry.register_provider(
                provider_type="kubernetes",
                strategy_factory=create_kubernetes_strategy,
                config_factory=create_kubernetes_config,
                resolver_factory=create_kubernetes_resolver,
                validator_factory=create_kubernetes_validator,
                strategy_class=KubernetesProviderStrategy,
                default_api="KubernetesPod",
            )

        if logger:
            logger.info("Kubernetes provider registered successfully")

    except Exception as exc:
        if logger:
            logger.error("Failed to register Kubernetes provider: %s", exc)
        raise


def initialize_kubernetes_provider(
    template_factory: "Optional[TemplateFactory]" = None,
    logger: "Optional[LoggingPort]" = None,
) -> None:
    """Initialize Kubernetes provider components.

    Mirrors :func:`orb.providers.aws.registration.initialize_aws_provider`.
    Each registration is wrapped in :func:`contextlib.suppress(ImportError)`
    so Phase A imports cleanly even when phase-B+ modules (CLI spec, field
    mapping, template adapter, example generator) are not yet present.
    """
    try:
        register_kubernetes_provider_settings()
        register_kubernetes_extensions(logger)
        register_kubernetes_auth_strategies(logger)

        if template_factory is not None:
            register_kubernetes_template_factory(template_factory, logger)

        # CLI spec — arrives in Phase G.
        with suppress(ImportError):
            from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
            from orb.providers.kubernetes.cli.kubernetes_cli_spec import (  # type: ignore[import-not-found]  # noqa: PLC0415
                KubernetesCLISpec,
            )

            CLISpecRegistry.register("kubernetes", KubernetesCLISpec())

        # HostFactory field mapping — arrives in Phase G.
        with suppress(ImportError):
            from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import (
                FieldMappingRegistry,
            )
            from orb.providers.kubernetes.scheduler.hostfactory_field_mapping import (  # type: ignore[import-not-found]  # noqa: PLC0415
                KubernetesFieldMapping,
            )

            FieldMappingRegistry.register("kubernetes", KubernetesFieldMapping())

        # Defaults loader — Phase A loader always available (returns {} for now).
        from orb.providers.kubernetes.defaults_loader import KubernetesDefaultsLoader
        from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry

        DefaultsLoaderRegistry.register("kubernetes", KubernetesDefaultsLoader())

        if logger:
            logger.info("Kubernetes provider initialization completed successfully")

    except Exception as exc:
        error_msg = f"Kubernetes provider initialization failed: {exc}"
        if logger:
            logger.error(error_msg, exc_info=True)
        raise


def register_kubernetes_services_with_di(container) -> None:
    """Register Kubernetes utility services with the DI container.

    Phase A: registers the ``TemplateAdapterPort`` / ``TemplateExampleGeneratorPort``
    bindings only when their concrete implementations exist (Phase B+).  In
    Phase A this function is a documented no-op.
    """
    from orb.domain.base.ports import LoggingPort

    logger = container.get(LoggingPort)

    with suppress(ImportError):
        from orb.domain.base.ports.template_adapter_port import TemplateAdapterPort
        from orb.providers.kubernetes.infrastructure.adapters.template_adapter import (  # type: ignore[import-not-found]  # noqa: PLC0415
            KubernetesTemplateAdapter,
            create_kubernetes_template_adapter,
        )

        container.register_singleton(KubernetesTemplateAdapter, create_kubernetes_template_adapter)
        container.register_singleton(TemplateAdapterPort, create_kubernetes_template_adapter)
        logger.debug("Kubernetes Template Adapter registered with DI container")

    with suppress(ImportError):
        from orb.domain.base.ports.template_example_generator_port import (
            TemplateExampleGeneratorPort,
        )
        from orb.providers.kubernetes.adapters.template_example_generator_adapter import (  # type: ignore[import-not-found]  # noqa: PLC0415
            KubernetesTemplateExampleGeneratorAdapter,
            create_kubernetes_template_example_generator,
        )

        container.register_singleton(
            TemplateExampleGeneratorPort, create_kubernetes_template_example_generator
        )
        # Concrete class registered as well so callers can ``container.get(...)``
        # against either type.
        container.register_singleton(
            KubernetesTemplateExampleGeneratorAdapter,
            create_kubernetes_template_example_generator,
        )
        logger.debug("Kubernetes TemplateExampleGeneratorPort registered with DI container")


# ---------------------------------------------------------------------------
# Introspection helper
# ---------------------------------------------------------------------------


def is_kubernetes_provider_registered() -> bool:
    """Return ``True`` when the kubernetes provider's settings class is registered."""
    try:
        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry

        return "kubernetes" in ProviderSettingsRegistry.get_registered_provider_types()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Auto-register settings + extensions when the module is imported.
# Matches the AWS provider's behaviour so basic functionality is available
# even when ``initialize_kubernetes_provider`` is not explicitly invoked.
# ---------------------------------------------------------------------------

with suppress(Exception):
    register_kubernetes_provider_settings()
    register_kubernetes_extensions()
