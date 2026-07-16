"""Abstract base class for structured provider onboarding.

A provider plugin declares itself by subclassing :class:`ProviderPlugin` and
implementing the mandatory satellite accessors.  The orchestrated lifecycle
methods :meth:`register_provider`, :meth:`initialize_provider`, and
:meth:`register_services_with_di` are provided by the base class and call the
satellite accessors in the correct order, so concrete subclasses only need to
supply the provider-specific pieces.

Usage (new provider)::

    class AzurePlugin(ProviderPlugin):
        provider_name = "azure"

        def strategy_factory(self): ...
        def config_factory(self): ...
        def template_dto_config(self): ...
        def cli_spec(self): ...
        def field_mapping(self): ...
        def defaults_loader(self): ...
        def template_example_generator(self, container): ...

Then wire it in ``pyproject.toml``::

    [project.entry-points."orb.providers"]
    azure = "orb.providers.azure.provider_plugin:AzurePlugin.register_plugin"

No shared ORB file edits are required.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    pass

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level idempotency guard
# ---------------------------------------------------------------------------

_initialized_providers: set[str] = set()
"""Names of providers whose :meth:`ProviderPlugin.initialize_provider` has
completed successfully.

Checked by :meth:`ProviderPlugin.initialize_provider` to prevent double-init
when the entry-point hook fires more than once (e.g. during test isolation
resets that re-import the entry-point group).

Call :func:`reset_for_testing` in ``pytest`` fixtures to clear this between
tests that exercise the full bootstrap path.
"""


def reset_for_testing() -> None:
    """Clear the initialized-provider guard set.

    **Test-only helper.**  Production code must never call this.  Use in
    ``pytest`` fixtures that exercise the full bootstrap path and need each
    test to start with a clean provider state::

        @pytest.fixture(autouse=True)
        def _clear_provider_state():
            reset_for_testing()
            yield
            reset_for_testing()
    """
    _initialized_providers.clear()


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class ProviderPlugin(ABC):
    """Abstract base for structured provider onboarding.

    Subclasses must set :attr:`provider_name` and implement all ``@abstractmethod``
    satellite accessors.  The orchestrated lifecycle is then available for free
    via :meth:`register_provider`, :meth:`initialize_provider`, and
    :meth:`register_services_with_di`.

    **Thread-safety:** The module-level ``_initialized_providers`` set is
    checked and written from the main bootstrap thread only.  If a provider
    plugin is ever bootstrapped from multiple threads simultaneously, callers
    are responsible for external locking.
    """

    #: Canonical snake_case provider name (e.g. ``"aws"``, ``"k8s"``, ``"azure"``).
    #: Subclasses MUST override this class attribute.
    provider_name: str = ""

    # ------------------------------------------------------------------
    # Mandatory satellite accessors — must be implemented by subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    def strategy_factory(self) -> Any:
        """Return the callable that creates a provider strategy instance.

        The returned callable must accept a single ``provider_config`` argument
        (a typed config object, a ``ProviderInstanceConfig``, or a raw dict)
        and return an initialised strategy instance.

        Example::

            def strategy_factory(self):
                from orb.providers.aws.registration import create_aws_strategy
                return create_aws_strategy
        """

    @abstractmethod
    def config_factory(self) -> Any:
        """Return the callable that creates a typed provider config object.

        The returned callable must accept a ``dict`` and return an instance of
        the provider's ``ProviderConfig`` class (typically a ``BaseSettings``
        subclass).
        """

    @abstractmethod
    def template_dto_config(self) -> Any:
        """Return the provider's ``TemplateDTOConfig`` class (not an instance).

        This class is registered with
        :class:`~orb.infrastructure.registry.template_extension_registry.TemplateExtensionRegistry`
        so that :class:`~orb.domain.template.template_dto.TemplateDTO` can
        deserialise provider-specific fields.

        Return ``None`` if the provider has no DTO config extension.
        """

    @abstractmethod
    def cli_spec(self) -> Any:
        """Return an instance of the provider's ``ProviderCLISpecPort`` implementation.

        The instance is registered with
        :class:`~orb.infrastructure.registry.cli_spec_registry.CLISpecRegistry`
        under :attr:`provider_name`.

        Return ``None`` to skip CLI spec registration.
        """

    @abstractmethod
    def field_mapping(self) -> Any:
        """Return an instance of the provider's HostFactory field-mapping adapter.

        The instance is registered with
        :class:`~orb.infrastructure.scheduler.hostfactory.field_mapping_registry.FieldMappingRegistry`
        under :attr:`provider_name`.

        Return ``None`` to skip field-mapping registration.
        """

    @abstractmethod
    def defaults_loader(self) -> Any:
        """Return an instance of the provider's
        :class:`~orb.domain.base.ports.provider_defaults_loader_port.ProviderDefaultsLoaderPort`
        implementation.

        The instance is registered with
        :class:`~orb.providers.registry.defaults_loader_registry.DefaultsLoaderRegistry`
        under :attr:`provider_name`.

        Return ``None`` to skip defaults-loader registration.
        """

    @abstractmethod
    def template_example_generator(self, container: Any) -> Any:
        """Return an instance of the provider's ``TemplateExampleGeneratorPort`` implementation.

        Called during :meth:`register_services_with_di` once the DI container
        is available.  The returned instance is registered with
        :class:`~orb.infrastructure.registry.template_example_generator_registry.TemplateExampleGeneratorRegistry`
        under :attr:`provider_name`.

        Args:
            container: The resolved DI container (passed through from
                :meth:`register_services_with_di`).

        Return ``None`` to skip example-generator registration.
        """

    # ------------------------------------------------------------------
    # Optional hooks — may be overridden but have default no-op behaviour
    # ------------------------------------------------------------------

    def resolver_factory(self) -> Optional[Any]:
        """Return the callable that creates a template resolver, or ``None``.

        Defaults to ``None`` (no resolver).  Passed to
        :meth:`~orb.providers.registry.provider_registry.ProviderRegistry.register_provider`
        as ``resolver_factory``.
        """
        return None

    def validator_factory(self) -> Optional[Any]:
        """Return the callable that creates a template validator, or ``None``.

        Defaults to ``None`` (no validator).  Passed to
        :meth:`~orb.providers.registry.provider_registry.ProviderRegistry.register_provider`
        as ``validator_factory``.
        """
        return None

    def strategy_class(self) -> Optional[type]:
        """Return the concrete strategy class (used for isinstance checks), or ``None``.

        Defaults to ``None``.  When provided, passed to
        :meth:`~orb.providers.registry.provider_registry.ProviderRegistry.register_provider`
        as ``strategy_class``.
        """
        return None

    def default_api(self) -> Optional[str]:
        """Return the default API name for this provider, or ``None``.

        Defaults to ``None``.  When provided, passed to
        :meth:`~orb.providers.registry.provider_registry.ProviderRegistry.register_provider`
        as ``default_api``.
        """
        return None

    def provider_settings_class(self) -> Optional[type]:
        """Return the ``BaseSettings`` subclass for this provider's config-file section.

        When not ``None``, registered with
        :class:`~orb.config.schemas.provider_settings_registry.ProviderSettingsRegistry`
        under :attr:`provider_name` during :meth:`initialize_provider`.

        Defaults to ``None``.
        """
        return None

    def template_class(self) -> Optional[type]:
        """Return the provider-specific ``TemplateAggregate`` subclass, or ``None``.

        When not ``None`` and a ``template_factory`` is supplied to
        :meth:`initialize_provider`, the class is registered via
        :meth:`~orb.domain.template.factory.TemplateFactory.register_provider_template_class`.

        Defaults to ``None``.
        """
        return None

    def register_auth_strategies(self, logger: Optional[Any] = None) -> None:
        """Register provider-specific auth strategies with the auth registry.

        Override to wire authentication strategies (e.g. IAM, Cognito).
        The default implementation is a no-op.

        Args:
            logger: Optional :class:`~orb.domain.base.ports.LoggingPort` instance.
        """

    def register_additional_services(self, container: Any, logger: Optional[Any] = None) -> None:
        """Register provider-specific DI services beyond the template adapter.

        Override to wire extra singletons (e.g. a cache service, a native-spec
        service, an image resolver).  Called from :meth:`register_services_with_di`
        before the template-example-generator registration.

        The default implementation is a no-op.

        Args:
            container: Resolved DI container.
            logger: Optional :class:`~orb.domain.base.ports.LoggingPort` instance.
        """

    def _do_initialize(self, logger: Optional[Any] = None) -> None:
        """Run provider-specific extra initialization steps.

        Called at the end of :meth:`initialize_provider`, after all standard
        satellite registrations (settings, DTO extension, auth strategies, CLI
        spec, field mapping, defaults loader) have completed successfully.

        Override in subclasses to perform any additional initialization that
        must happen after the standard satellites but is not covered by the
        existing hooks.  Examples: registering storage backends, wiring retry
        classifiers, or adding provider-specific resilience configuration.

        The default implementation is a no-op.

        Args:
            logger: Optional :class:`~orb.domain.base.ports.LoggingPort` instance.
        """

    # ------------------------------------------------------------------
    # Orchestrated lifecycle methods
    # ------------------------------------------------------------------

    def register_provider(
        self,
        registry: Optional[Any] = None,
        logger: Optional[Any] = None,
        instance_name: Optional[str] = None,
    ) -> None:
        """Register this provider's strategy and config factories with the registry.

        Calls
        :meth:`~orb.providers.registry.provider_registry.ProviderRegistry.register_provider`
        (or ``register_provider_instance`` when *instance_name* is given) using
        the satellite accessor return values.

        This method is the registry-level registration step and is idempotent
        with respect to the registry's own guards.

        Args:
            registry: Live :class:`~orb.providers.registry.ProviderRegistry`
                instance.  Fetched from :func:`~orb.providers.registry.get_provider_registry`
                when omitted.
            logger: Optional :class:`~orb.domain.base.ports.LoggingPort`.
            instance_name: When supplied, registers a named provider instance
                instead of the provider type.
        """
        if registry is None:
            from orb.providers.registry import get_provider_registry

            registry = get_provider_registry()

        try:
            if instance_name:
                registry.register_provider_instance(
                    provider_type=self.provider_name,
                    instance_name=instance_name,
                    strategy_factory=self.strategy_factory(),
                    config_factory=self.config_factory(),
                    resolver_factory=self.resolver_factory(),
                    validator_factory=self.validator_factory(),
                )
            else:
                registry.register_provider(
                    provider_type=self.provider_name,
                    strategy_factory=self.strategy_factory(),
                    config_factory=self.config_factory(),
                    resolver_factory=self.resolver_factory(),
                    validator_factory=self.validator_factory(),
                    strategy_class=self.strategy_class(),
                    default_api=self.default_api(),
                )

            if logger:
                logger.info(
                    "%s provider registered successfully",
                    self.provider_name,
                )

        except Exception as exc:
            if logger:
                logger.error(
                    "Failed to register %s provider: %s",
                    self.provider_name,
                    exc,
                )
            raise

    def initialize_provider(
        self,
        template_factory: Optional[Any] = None,
        logger: Optional[Any] = None,
    ) -> None:
        """Initialise all provider satellites: settings, extensions, auth, CLI, etc.

        Uses the module-level :data:`_initialized_providers` set as an
        idempotency guard — a second call for the same provider name is a
        safe no-op.

        On failure the provider name is **not** added to the guard set, so a
        retry after fixing the underlying problem (e.g. a missing dependency)
        will re-attempt the full initialisation.

        Args:
            template_factory: Optional
                :class:`~orb.domain.template.factory.TemplateFactory` instance.
                When supplied, :meth:`template_class` is registered with it.
            logger: Optional :class:`~orb.domain.base.ports.LoggingPort`.
        """
        if self.provider_name in _initialized_providers:
            _logger.debug(
                "Provider %r already initialized — skipping",
                self.provider_name,
            )
            return

        try:
            # 1. Provider settings
            settings_cls = self.provider_settings_class()
            if settings_cls is not None:
                try:
                    from orb.config.schemas.provider_settings_registry import (
                        ProviderSettingsRegistry,
                    )

                    if ProviderSettingsRegistry.get_or_none(self.provider_name) is None:
                        ProviderSettingsRegistry.register_provider_settings(
                            self.provider_name, settings_cls
                        )
                except ImportError as _exc:
                    # ProviderSettingsRegistry not installed in this environment; skip silently.
                    _logger.debug(
                        "Skipping %r settings registration — optional dependency absent: %s",
                        self.provider_name,
                        _exc,
                    )

            # 2. Template DTO extension
            dto_config_cls = self.template_dto_config()
            if dto_config_cls is not None:
                try:
                    from orb.infrastructure.registry.template_extension_registry import (
                        TemplateExtensionRegistry,
                    )

                    if not TemplateExtensionRegistry.has_extension(self.provider_name):
                        TemplateExtensionRegistry.register_extension(
                            self.provider_name, dto_config_cls
                        )
                except ImportError as _exc:
                    # TemplateExtensionRegistry not installed in this environment; skip silently.
                    _logger.debug(
                        "Skipping %r DTO-extension registration — optional dependency absent: %s",
                        self.provider_name,
                        _exc,
                    )

            # 3. Auth strategies (optional hook)
            self.register_auth_strategies(logger)

            # 4. Template class (optional)
            tpl_cls = self.template_class()
            if tpl_cls is not None and template_factory is not None:
                try:
                    template_factory.register_provider_template_class(self.provider_name, tpl_cls)
                except Exception as exc:
                    _logger.debug(
                        "Could not register template class for %r: %s",
                        self.provider_name,
                        exc,
                    )

            # 5. CLI spec
            cli_spec_instance = self.cli_spec()
            if cli_spec_instance is not None:
                try:
                    from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry

                    CLISpecRegistry.register(self.provider_name, cli_spec_instance)
                except ImportError as _exc:
                    # CLISpecRegistry not installed in this environment; skip silently.
                    _logger.debug(
                        "Skipping %r CLI-spec registration — optional dependency absent: %s",
                        self.provider_name,
                        _exc,
                    )

            # 6. HostFactory field mapping
            field_mapping_instance = self.field_mapping()
            if field_mapping_instance is not None:
                try:
                    from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import (
                        FieldMappingRegistry,
                    )

                    FieldMappingRegistry.register(self.provider_name, field_mapping_instance)
                except ImportError as _exc:
                    # FieldMappingRegistry not installed in this environment; skip silently.
                    _logger.debug(
                        "Skipping %r field-mapping registration — optional dependency absent: %s",
                        self.provider_name,
                        _exc,
                    )

            # 7. Defaults loader
            loader_instance = self.defaults_loader()
            if loader_instance is not None:
                try:
                    from orb.providers.registry.defaults_loader_registry import (
                        DefaultsLoaderRegistry,
                    )

                    DefaultsLoaderRegistry.register(self.provider_name, loader_instance)
                except ImportError as _exc:
                    # DefaultsLoaderRegistry not installed in this environment; skip silently.
                    _logger.debug(
                        "Skipping %r defaults-loader registration — optional dependency absent: %s",
                        self.provider_name,
                        _exc,
                    )

            # 8. Provider-specific extra initialization (optional hook)
            self._do_initialize(logger)

            _initialized_providers.add(self.provider_name)

            if logger:
                logger.info(
                    "%s provider initialization completed successfully",
                    self.provider_name,
                )

        except Exception as exc:
            # Deliberately NOT adding to _initialized_providers so a retry
            # after fixing the root cause will re-attempt fully.
            error_msg = f"{self.provider_name} provider initialization failed: {exc}"
            if logger:
                logger.error(error_msg, exc_info=True)
            raise

    def register_services_with_di(self, container: Any) -> None:
        """Register provider utility services with the DI container.

        Calls :meth:`register_additional_services` (for provider-specific
        extra wiring) and then registers the :meth:`template_example_generator`
        result with
        :class:`~orb.infrastructure.registry.template_example_generator_registry.TemplateExampleGeneratorRegistry`.

        Args:
            container: Resolved DI container.
        """
        try:
            from orb.domain.base.ports import LoggingPort

            logger = container.get(LoggingPort)
        except Exception:
            logger = None

        try:
            # Provider-specific additional services (optional hook)
            self.register_additional_services(container, logger)

            # Template example generator
            gen_instance = self.template_example_generator(container)
            if gen_instance is not None:
                try:
                    from orb.infrastructure.registry.template_example_generator_registry import (
                        TemplateExampleGeneratorRegistry,
                    )

                    TemplateExampleGeneratorRegistry.register(self.provider_name, gen_instance)
                    if logger:
                        logger.debug(
                            "%s TemplateExampleGeneratorAdapter registered",
                            self.provider_name,
                        )
                except ImportError as _exc:
                    # TemplateExampleGeneratorRegistry not installed; skip silently.
                    _logger.debug(
                        "Skipping %r example-generator registration — optional dependency absent: %s",
                        self.provider_name,
                        _exc,
                    )

            if logger:
                logger.debug(
                    "%s utility services registered with DI container",
                    self.provider_name,
                )

        except Exception as exc:
            if logger:
                logger.warning(
                    "Failed to register %s utility services with DI container: %s",
                    self.provider_name,
                    exc,
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Classmethods for entry-point and test lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def register_plugin(cls) -> None:
        """Entry-point hook for the ``orb.providers`` entry-point group.

        Zero-argument, idempotent.  Constructs an instance of the concrete
        subclass and calls :meth:`register_provider` with the live registry.

        Wire this as the entry-point target in ``pyproject.toml``::

            [project.entry-points."orb.providers"]
            azure = "orb.providers.azure.provider_plugin:AzurePlugin.register_plugin"

        The method appends :attr:`provider_name` to the module-level
        ``_REGISTERED_PROVIDERS`` list in :mod:`orb.providers.registration`
        so that the bootstrap loops that iterate that list pick up the provider.
        """
        instance = cls()
        if not instance.provider_name:
            raise ValueError(f"{cls.__name__}.provider_name must be set to a non-empty string")

        # Register with the live provider registry
        instance.register_provider()

        # Append to the module-level discovery list so bootstrap loops find it
        try:
            import orb.providers.registration as _reg_mod

            if instance.provider_name not in _reg_mod._REGISTERED_PROVIDERS:
                _reg_mod._REGISTERED_PROVIDERS.append(instance.provider_name)
        except Exception as exc:
            _logger.warning(
                "Could not append %r to _REGISTERED_PROVIDERS: %s",
                instance.provider_name,
                exc,
            )

    @classmethod
    def reset_for_testing(cls) -> None:
        """Remove this provider from the initialized-provider guard set.

        Delegates to the module-level :func:`reset_for_testing` helper when
        called without arguments, i.e. clears ALL providers.

        **Test-only.**  Prefer the module-level :func:`reset_for_testing`
        function for fixtures that reset all providers; use this classmethod
        when a single provider's state should be cleared in isolation::

            AWSPlugin.reset_for_testing()
        """
        _initialized_providers.discard(cls().provider_name if cls.provider_name else "")


__all__ = ["ProviderPlugin", "reset_for_testing"]
