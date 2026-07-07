"""Kubernetes Provider Registration.

Mirrors :mod:`orb.providers.aws.registration` for the modern kubernetes
provider.  Hits every registry the AWS provider hits so the kubernetes
provider has parity with the AWS integration surface from day one:

* ``ProviderRegistry``           — strategy / config / resolver / validator factories
* ``ProviderSettingsRegistry``   — typed BaseSettings class for config-file parsing
* ``CLISpecRegistry``            — only when the CLI spec module is present
* ``FieldMappingRegistry``       — only when the HostFactory field-mapping module is present
* ``DefaultsLoaderRegistry``     — provider defaults loader
* ``AuthRegistry``               — provider-side auth strategies (placeholder)
* ``TemplateExtensionRegistry``  — only when the AWS-style DTO config exists
* ``TemplateAdapterPort``        — only when the AWS-style adapter exists
* ``TemplateExampleGeneratorPort`` — only when the example generator exists

Optional integrations are wrapped in defensive ``ImportError`` guards so
this module imports cleanly even when those modules are absent.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Any, Optional

from orb.providers.k8s.configuration.template_extension import (
    K8sTemplateExtensionConfig,
)

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.domain.base.ports import LoggingPort
    from orb.domain.template.factory import TemplateFactory
    from orb.providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Strategy / config / resolver / validator factories
# ---------------------------------------------------------------------------


def _k8s_config_is_empty(provider_config: Any) -> bool:
    """Return ``True`` when *provider_config* carries no cluster-targeting information.

    A config is considered empty when it is ``None``, an empty dict, or a raw
    dict that specifies none of the explicit cluster-targeting fields:
    ``kubeconfig_path``, ``context``, or ``in_cluster``.  In that situation the
    kubernetes client library falls back to whatever ``$KUBECONFIG`` or
    ``~/.kube/config`` happens to be loaded in the current environment, which
    risks silently targeting an unintended cluster.

    Note: ``in_cluster=True`` is an explicit targeting signal (operator
    deliberately opts in to in-cluster mode) and therefore not considered empty.
    ``in_cluster=False`` offers no useful targeting information on its own and
    is treated as empty.
    """
    if provider_config is None:
        return True
    if isinstance(provider_config, dict):
        return (
            not provider_config.get("kubeconfig_path")
            and not provider_config.get("context")
            and not provider_config.get("in_cluster")
        )
    return False


def create_k8s_strategy(provider_config: Any) -> Any:
    """Create a :class:`K8sProviderStrategy` from configuration.

    Accepts a :class:`K8sProviderConfig`, a ``ProviderInstanceConfig``,
    or a raw config dict.  The DI container is consulted opportunistically
    for ``ConfigurationPort`` and ``ConsolePort`` — failures are logged at
    debug level and the strategy is constructed without them.

    Args:
        provider_config: Provider configuration — a :class:`K8sProviderConfig`,
            a ``ProviderInstanceConfig``, or a raw dict.

    Raises:
        RuntimeError: When *provider_config* is ``None`` or carries no
            cluster-targeting information (no ``kubeconfig_path``, no
            ``context``).  Without explicit targeting the kubernetes client
            would silently connect to whatever cluster the ambient
            ``$KUBECONFIG`` points at, which risks submitting pods to an
            unintended cluster.  Callers that legitimately want in-cluster
            auto-detection must pass ``{"in_cluster": True}`` explicitly,
            or supply a proper :class:`K8sProviderConfig` instance.
    """
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.strategy.k8s_provider_strategy import (
        K8sProviderStrategy,
    )

    try:
        if isinstance(provider_config, K8sProviderConfig):
            k8s_config = provider_config
            provider_instance_config = None
            provider_name = None
        elif hasattr(provider_config, "config"):
            config_data = provider_config.config
            provider_instance_config = provider_config
            provider_name = provider_config.name
            # Apply the empty-config guard on the extracted dict regardless of
            # which branch produced it — ProviderInstanceConfig(config={}) would
            # otherwise bypass the check.
            if _k8s_config_is_empty(config_data):
                raise RuntimeError(
                    "Cannot create a Kubernetes strategy without explicit cluster-targeting "
                    "configuration (kubeconfig_path or context).  "
                    "The instance config is missing or empty; ORB refuses to fall back to "
                    "the ambient $KUBECONFIG default to avoid accidentally targeting an "
                    "unintended cluster.  "
                    "Ensure the provider instance is present in config.json, or pass "
                    "{'in_cluster': True} when running inside a Kubernetes pod."
                )
            k8s_config = K8sProviderConfig(**config_data)
        else:
            config_data = provider_config
            # Reject empty / None configs before constructing a strategy that
            # would silently connect to the wrong cluster.
            if _k8s_config_is_empty(config_data):
                raise RuntimeError(
                    "Cannot create a Kubernetes strategy without explicit cluster-targeting "
                    "configuration (kubeconfig_path or context).  "
                    "The instance config is missing or empty; ORB refuses to fall back to "
                    "the ambient $KUBECONFIG default to avoid accidentally targeting an "
                    "unintended cluster.  "
                    "Ensure the provider instance is present in config.json, or pass "
                    "{'in_cluster': True} when running inside a Kubernetes pod."
                )
            provider_instance_config = None
            provider_name = None
            k8s_config = K8sProviderConfig(**(config_data or {}))

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

        # Resolve NativeSpecService at strategy construction time so the
        # strategy does not need to import from orb.application at call time.
        # This keeps the providers→application dependency out of method bodies
        # and contained to DI wiring only.
        native_spec_service = None
        try:
            from orb.application.services.native_spec_service import (
                NativeSpecService,
            )
            from orb.infrastructure.di.container import get_container

            native_spec_service = get_container().get(NativeSpecService)
        except Exception as exc:
            logger.debug("Could not get NativeSpecService from DI container: %s", exc)

        strategy = K8sProviderStrategy(
            config=k8s_config,
            logger=logger,
            provider_name=provider_name,
            provider_instance_config=provider_instance_config,
            config_port=config_port,
            console=console_port,
            native_spec_service=native_spec_service,
        )

        if not strategy.initialize():
            raise RuntimeError("Failed to initialize Kubernetes provider strategy")

        with suppress(Exception):
            from orb.domain.base.ports.health_check_port import HealthCheckPort
            from orb.infrastructure.di.container import get_container
            from orb.providers.k8s.health import register_k8s_health_checks

            health_check = get_container().get(HealthCheckPort)
            register_k8s_health_checks(health_check, strategy.kubernetes_client)

        if provider_name:
            strategy._provider_name = provider_name  # type: ignore[assignment]

        return strategy

    except ImportError as exc:
        raise ImportError(f"Kubernetes provider strategy not available: {exc!s}")
    except Exception as exc:
        raise RuntimeError(f"Failed to create Kubernetes strategy: {exc!s}")


def create_k8s_config(data: dict[str, Any]) -> Any:
    """Create :class:`K8sProviderConfig` from a dict of values."""
    try:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        return K8sProviderConfig(**data)
    except ImportError as exc:
        raise ImportError(f"Kubernetes configuration not available: {exc!s}")
    except Exception as exc:
        raise RuntimeError(f"Failed to create Kubernetes config: {exc!s}")


def create_k8s_resolver() -> Any:
    """No provider-side template resolver is needed."""
    return None


def create_k8s_validator(provider_config: Any = None) -> Any:
    """Create a :class:`K8sTemplateValidator` for registration-time template checks.

    The validator is called by the provider registry at template-registration
    time so that malformed templates are rejected before the first acquire
    attempt reaches the Kubernetes API server.

    Args:
        provider_config: Unused; accepted for API parity with the AWS
            equivalent so the registry can call all validator factories
            with the same signature.

    Returns:
        A :class:`~orb.providers.k8s.validation.template_validator.K8sTemplateValidator`
        instance.
    """
    from orb.providers.k8s.validation.template_validator import (
        K8sTemplateValidator,
    )

    return K8sTemplateValidator()


# ---------------------------------------------------------------------------
# Auxiliary registrations
# ---------------------------------------------------------------------------


def register_k8s_provider_settings() -> None:
    """Register :class:`K8sProviderConfig` with the provider settings registry."""
    try:
        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        ProviderSettingsRegistry.register_provider_settings("k8s", K8sProviderConfig)
    except ImportError:
        # Settings registry not available — operator must not be running
        # the configuration loader path; nothing to do.
        pass
    except Exception as exc:
        raise RuntimeError(f"Failed to register Kubernetes provider settings: {exc!s}")


def register_k8s_extensions(logger: Optional[LoggingPort] = None) -> None:
    """Register template-DTO extensions with the global template extension registry.

    Registers :class:`K8sTemplateDTOConfig` as the typed
    ``provider_config`` class for :class:`TemplateDTO` serialisation so the
    kubernetes-specific fields (``container_image``, ``namespace``, resource
    requests / limits, etc.) round-trip cleanly without leaking into the
    generic template DTO.
    """
    try:
        from orb.infrastructure.registry.template_extension_registry import (
            TemplateExtensionRegistry,
        )
        from orb.providers.k8s.domain.template.k8s_template_dto_config import (
            K8sTemplateDTOConfig,
        )

        TemplateExtensionRegistry.register_extension("k8s", K8sTemplateDTOConfig)
        if logger:
            logger.debug("Kubernetes template extensions registered successfully")
    except Exception as exc:
        if logger:
            logger.error(
                "Failed to register Kubernetes template extensions: %s", exc, exc_info=True
            )
        raise


def get_k8s_extension_defaults() -> dict[str, Any]:
    """Return default kubernetes extension configuration values.

    Mirrors :func:`orb.providers.aws.registration.get_aws_extension_defaults`.
    Returns the kubernetes-specific defaults from
    :class:`K8sTemplateExtensionConfig` so callers (template merge,
    docs generation, CLI introspection) can introspect the baseline without
    materialising an instance manually.
    """
    return K8sTemplateExtensionConfig().to_template_defaults()  # type: ignore[call-arg]


def register_k8s_auth_strategies(logger: Optional[LoggingPort] = None) -> None:
    """Register Kubernetes auth strategies with the auth registry.

    The kubernetes provider does not currently ship an inbound HTTP auth
    strategy.  The kube-API auth helpers
    (:mod:`orb.providers.k8s.auth.in_cluster` and
    :mod:`orb.providers.k8s.auth.kubeconfig`) are separate concerns —
    they bootstrap the kubernetes API client, not the ORB REST surface.
    This function is wired into ``initialize_k8s_provider`` so the
    integration point exists; concrete strategies can be added later.
    """
    if logger:
        logger.debug(
            "Kubernetes provider has no inbound HTTP auth strategies; "
            "kube-API auth is handled via providers.k8s.auth.*"
        )


def register_k8s_template_factory(
    factory: TemplateFactory, logger: Optional[LoggingPort] = None
) -> None:
    """Register the Kubernetes template class with the template factory.

    When the concrete template aggregate is unavailable the registration
    is a defensive no-op that logs at debug level so callers can keep
    importing this module unconditionally.
    """
    try:
        from orb.providers.k8s.domain.template.k8s_template import (
            K8sTemplate,
        )

        factory.register_provider_template_class("k8s", K8sTemplate)
        if logger:
            logger.info("Kubernetes template class registered with factory")
    except ImportError:
        if logger:
            logger.debug(
                "Kubernetes template aggregate not available; skipping factory registration."
            )
    except Exception as exc:
        if logger:
            logger.warning(
                "Failed to register Kubernetes template factory: %s",
                exc,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Provider-registry entry point
# ---------------------------------------------------------------------------


def register_k8s_provider(
    registry: Optional[ProviderRegistry] = None,
    logger: Optional[LoggingPort] = None,
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
        from orb.providers.k8s.strategy.k8s_provider_strategy import (
            K8sProviderStrategy,
        )

        if instance_name:
            registry.register_provider_instance(
                provider_type="k8s",
                instance_name=instance_name,
                strategy_factory=create_k8s_strategy,
                config_factory=create_k8s_config,
                resolver_factory=create_k8s_resolver,
                validator_factory=create_k8s_validator,
            )
        else:
            registry.register_provider(
                provider_type="k8s",
                strategy_factory=create_k8s_strategy,
                config_factory=create_k8s_config,
                resolver_factory=create_k8s_resolver,
                validator_factory=create_k8s_validator,
                strategy_class=K8sProviderStrategy,
                default_api="Pod",
            )

        if logger:
            logger.info("Kubernetes provider registered successfully")

    except Exception as exc:
        if logger:
            logger.error("Failed to register Kubernetes provider: %s", exc)
        raise


def register_k8s_provider_instance(provider_instance, logger=None) -> bool:
    """Register a configured Kubernetes provider instance with the registry.

    Called by
    :meth:`orb.providers.registry.provider_registry.ProviderRegistryImpl.ensure_provider_instance_registered_from_config`
    when the application starts up and finds a configured ``k8s`` provider
    entry under ``config.json``.  Mirrors
    :func:`orb.providers.aws.registration.register_aws_provider_instance`.
    """
    try:
        if logger:
            logger.debug("Registering Kubernetes provider instance: %s", provider_instance.name)

        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()

        if not registry.is_provider_registered("k8s"):
            from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

            registry.register_provider(
                provider_type="k8s",
                strategy_factory=create_k8s_strategy,
                config_factory=create_k8s_config,
                resolver_factory=create_k8s_resolver,
                validator_factory=create_k8s_validator,
                strategy_class=K8sProviderStrategy,
                default_api="Pod",
            )

        registry.register_provider_instance(
            provider_type="k8s",
            instance_name=provider_instance.name,
            strategy_factory=create_k8s_strategy,
            config_factory=create_k8s_config,
            resolver_factory=create_k8s_resolver,
            validator_factory=create_k8s_validator,
        )

        if logger:
            logger.debug(
                "Successfully registered Kubernetes provider instance: %s",
                provider_instance.name,
            )
        return True

    except Exception as exc:
        # Extract the config snippet that was attempted so operators can diagnose
        # the failure without grepping code.
        config_data = getattr(provider_instance, "config", None) or {}
        config_snippet = {
            k: config_data.get(k) for k in ("kubeconfig_path", "context", "namespace")
        }
        if logger:
            logger.error(
                "Failed to register Kubernetes provider instance '%s': %s  "
                "(config keys attempted: kubeconfig_path=%r, context=%r, namespace=%r)",
                provider_instance.name,
                exc,
                config_snippet.get("kubeconfig_path"),
                config_snippet.get("context"),
                config_snippet.get("namespace"),
                exc_info=True,
            )
        return False


def initialize_k8s_provider(
    template_factory: Optional[TemplateFactory] = None,
    logger: Optional[LoggingPort] = None,
) -> None:
    """Initialize Kubernetes provider components.

    Mirrors :func:`orb.providers.aws.registration.initialize_aws_provider`.
    Wires every registry the provider participates in: provider settings,
    template DTO extensions, auth strategies, the optional template factory,
    the CLI spec, the HostFactory field-mapping adapter, and the defaults
    loader.
    """
    try:
        register_k8s_provider_settings()
        register_k8s_extensions(logger)
        register_k8s_auth_strategies(logger)

        if template_factory is not None:
            register_k8s_template_factory(template_factory, logger)

        # Retry classifier — routes k8s non-retryable 4xx codes through the
        # provider-agnostic registry so the resilience layer needs no direct
        # kubernetes SDK import.
        from orb.infrastructure.resilience.retry_classifier_registry import (
            register_retry_classifier,
        )
        from orb.providers.k8s.resilience.retry_classifier import K8sRetryClassifier

        register_retry_classifier(K8sRetryClassifier())

        # CLI spec
        from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
        from orb.providers.k8s.cli.k8s_cli_spec import K8sCLISpec

        CLISpecRegistry.register("k8s", K8sCLISpec())

        # HostFactory field mapping
        from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import (
            FieldMappingRegistry,
        )
        from orb.providers.k8s.scheduler.hostfactory_field_mapping import (
            K8sFieldMapping,
        )

        FieldMappingRegistry.register("k8s", K8sFieldMapping())

        # Defaults loader
        from orb.providers.k8s.defaults_loader import KubernetesDefaultsLoader
        from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry

        DefaultsLoaderRegistry.register("k8s", KubernetesDefaultsLoader())

        if logger:
            logger.info("Kubernetes provider initialization completed successfully")

    except Exception as exc:
        error_msg = f"Kubernetes provider initialization failed: {exc}"
        if logger:
            logger.error(error_msg, exc_info=True)
        raise


def register_k8s_services_with_di(container) -> None:
    """Register Kubernetes utility services with the DI container.

    Registers :class:`K8sTemplateAdapter` against both its concrete
    type and the :class:`TemplateAdapterPort` port so callers can resolve
    either binding from the container.

    Also registers :class:`K8sNativeSpecService` so plugin code and tests
    can resolve it from the container when the kubernetes provider's
    native-spec escape hatch is in use.  :func:`create_k8s_strategy`
    resolves the service from this container binding at strategy-creation
    time and passes it as a constructor argument, removing any in-method
    ``get_container()`` call from the strategy itself.

    **Infrastructure discovery service** (:class:`K8sInfrastructureDiscoveryService`)
    is intentionally NOT registered in the DI container.  It depends on
    :class:`K8sProviderConfig`, which is a per-strategy-instance value and
    therefore not resolvable from a global container.  The strategy constructs
    and owns the service lazily via
    :meth:`K8sProviderStrategy._get_discovery_service`, mirroring the AWS
    pattern (``AWSProviderStrategy._get_infrastructure_service``).

    The :class:`TemplateExampleGeneratorPort` registration is wrapped in
    ``suppress(ImportError)`` for defensive resilience; the concrete adapter
    lives under ``providers/k8s/adapters/template_example_generator_adapter``.
    """
    from orb.domain.base.ports import LoggingPort
    from orb.domain.base.ports.template_adapter_port import TemplateAdapterPort
    from orb.providers.k8s.infrastructure.adapters.template_adapter import (
        K8sTemplateAdapter,
        create_k8s_template_adapter,
    )

    logger = container.get(LoggingPort)

    container.register_singleton(K8sTemplateAdapter, create_k8s_template_adapter)
    container.register_singleton(TemplateAdapterPort, create_k8s_template_adapter)
    logger.debug("Kubernetes Template Adapter registered with DI container")

    with suppress(ImportError):
        from orb.domain.base.ports.configuration_port import ConfigurationPort
        from orb.providers.k8s.infrastructure.services.k8s_native_spec_service import (
            K8sNativeSpecService,
        )

        def _create_k8s_native_spec_service(_container) -> K8sNativeSpecService:
            # NativeSpecService lives in the application layer; import is deferred
            # to this factory closure so the providers layer does not carry a
            # static providers→application dependency.
            from orb.application.services.native_spec_service import (
                NativeSpecService,
            )

            return K8sNativeSpecService(
                native_spec_service=_container.get(NativeSpecService),
                config_port=_container.get(ConfigurationPort),
            )

        container.register_singleton(K8sNativeSpecService, _create_k8s_native_spec_service)
        logger.debug("K8sNativeSpecService registered with DI container")

    # Register the Kubernetes template example generator into the per-provider
    # registry.  The suppress guard is retained for defensive resilience only.
    with suppress(ImportError):
        from orb.infrastructure.registry.template_example_generator_registry import (
            TemplateExampleGeneratorRegistry,
        )
        from orb.providers.k8s.adapters.template_example_generator_adapter import (
            create_k8s_template_example_generator,
        )

        TemplateExampleGeneratorRegistry.register(
            "k8s", create_k8s_template_example_generator(container)
        )
        logger.debug(
            "Kubernetes TemplateExampleGeneratorAdapter registered in TemplateExampleGeneratorRegistry"
        )


# ---------------------------------------------------------------------------
# Introspection helper
# ---------------------------------------------------------------------------


def is_k8s_provider_registered() -> bool:
    """Return ``True`` when the kubernetes provider's settings class is registered."""
    try:
        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry

        return "k8s" in ProviderSettingsRegistry.get_registered_provider_types()
    except Exception:
        # Provider-settings registry may be unavailable during early bootstrap
        # or in stripped test environments; treat as "not registered".
        return False


# Settings and extensions are registered via the explicit lifecycle hook
# ``initialize_k8s_provider``.  No module-level auto-registration is
# performed here; both calls live inside that function and are invoked at
# application startup through the normal provider initialisation path.
