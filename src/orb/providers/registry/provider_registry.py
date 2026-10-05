"""Provider Registry - Registry pattern for provider strategy factories."""

import importlib
import re
import threading
from typing import TYPE_CHECKING, Any, Callable, List, Optional

# Only allow simple snake_case identifiers as provider types to prevent
# module-injection via crafted provider_type strings (e.g. containing dots
# or path-traversal sequences) that would be interpolated directly into the
# dynamic importlib.import_module() call.
_VALID_PROVIDER_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

from orb.domain.base.exceptions import ConfigurationError
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort
from orb.domain.base.results import ProviderSelectionResult
from orb.infrastructure.registry.base_registry import BaseRegistration, BaseRegistry, RegistryMode
from orb.infrastructure.utilities.common.string_utils import extract_provider_type
from orb.providers.registry.types import ProviderRegistration, UnsupportedProviderError

if TYPE_CHECKING:
    from orb.infrastructure.services.provider_selection_service import ProviderSelectionService


class ProviderRegistry(BaseRegistry, ProviderRegistryPort):
    """
    Registry for provider strategy factories.

    Uses MULTI_CHOICE mode - multiple provider strategies simultaneously.
    Thread-safe singleton implementation using BaseRegistry.
    """

    def __init__(self, config_port: Optional[ConfigurationPort] = None) -> None:
        # Provider is MULTI_CHOICE - multiple provider strategies simultaneously
        super().__init__(mode=RegistryMode.MULTI_CHOICE)
        self._strategy_cache: dict[str, Any] = {}
        self._health_states: dict[str, dict] = {}
        self._config_port = config_port
        self._fallback_strategy: Optional[Any] = None
        # Injected selection service; wired by bootstrap after construction.
        self._selection_service: Optional["ProviderSelectionService"] = None
        # Create logger directly since BaseRegistry no longer provides it
        from orb.infrastructure.logging.logger import get_logger

        self._logger = get_logger(__name__)

    def register_fallback_strategy(self, strategy: Any) -> None:
        """Register a fallback strategy used when no provider matches.

        Args:
            strategy: A constructed ProviderStrategy instance to use as fallback.
        """
        self._fallback_strategy = strategy

    def get_fallback_strategy(self) -> Optional[Any]:
        """Return the registered fallback strategy, or None if not set."""
        return self._fallback_strategy

    def get_strategy(self, provider_identifier: str) -> Optional[Any]:
        """Get cached strategy instance."""
        return self._strategy_cache.get(provider_identifier)

    def update_provider_health(self, provider_name: str, health_data: dict) -> None:
        """Store health state for a provider."""
        self._health_states[provider_name] = health_data

    def get_or_create_strategy(self, provider_identifier: str, config: Any = None) -> Optional[Any]:
        """
        Get cached strategy or create new one with auto-registration.

        Args:
            provider_identifier: Provider type or instance name
            config: Configuration (ProviderInstanceConfig object or dict)

        Returns:
            Strategy instance or None if creation failed
        """
        # Check cache first
        if provider_identifier in self._strategy_cache:
            return self._strategy_cache[provider_identifier]

        # Auto-register if needed
        if config and hasattr(config, "name") and hasattr(config, "type"):
            # It's a ProviderInstanceConfig object - register the instance
            self.ensure_provider_instance_registered_from_config(config)
        elif not self.is_provider_instance_registered(
            provider_identifier
        ) and not self.is_provider_registered(provider_identifier):
            # Try to auto-register the provider type
            provider_type = extract_provider_type(provider_identifier)
            self.ensure_provider_type_registered(provider_type)

        # Create new strategy
        strategy = None

        # Try instance creation first
        if self.is_provider_instance_registered(provider_identifier):
            # If no config provided, get the stored provider instance config
            if config is None:
                # Get the provider instance config from the injected configuration port
                try:
                    if self._config_port is None:
                        raise ConfigurationError("No configuration port available")
                    provider_config = self._config_port.get_provider_config()

                    if provider_config:
                        for instance in provider_config.get_active_providers():  # type: ignore[union-attr]
                            if instance.name == provider_identifier:
                                config = instance
                                break
                except Exception as e:
                    if self._logger:
                        self._logger.warning(
                            "Failed to retrieve provider instance config for %s: %s",
                            provider_identifier,
                            e,
                            exc_info=True,
                        )

            strategy = self.create_strategy_by_instance(provider_identifier, config)
        # Fall back to type creation when the identifier itself is the type.
        elif self.is_provider_registered(provider_identifier):
            strategy = self.create_strategy_by_type(provider_identifier, config)
        # Fall back to type creation when the identifier is an instance name
        # whose configured instance has been removed from config (e.g. terminating
        # leftover AWS machines after the operator switched the active provider
        # to k8s).  The provider type is still known, so the strategy can boot
        # with provider-side defaults (boto3 reads ~/.aws/credentials, etc.).
        else:
            provider_type = extract_provider_type(provider_identifier)
            if provider_type != provider_identifier and self.is_provider_registered(provider_type):
                if self._logger:
                    self._logger.info(
                        "Provider instance %r not registered; falling back to "
                        "%r type strategy with provider-side defaults so historical "
                        "machines for this instance can still be queried/terminated.",
                        provider_identifier,
                        provider_type,
                    )
                try:
                    strategy = self.create_strategy_by_type(provider_type, config)
                except Exception as fallback_exc:
                    if self._logger:
                        self._logger.warning(
                            "Type-level fallback strategy for instance %r (type %r) could not "
                            "be created; the instance will be treated as not found. "
                            "Reason: %s",
                            provider_identifier,
                            provider_type,
                            fallback_exc,
                        )
                    strategy = None

        if strategy:
            # Initialize strategy
            if hasattr(strategy, "initialize") and not strategy.is_initialized:
                if not strategy.initialize():
                    if self._logger:
                        self._logger.error(
                            "Failed to initialize strategy: %s", provider_identifier, exc_info=True
                        )
                    return None

            # Cache strategy
            self._strategy_cache[provider_identifier] = strategy

        return strategy

    def ensure_provider_type_registered(self, provider_type: str) -> bool:
        """
        Ensure provider type is registered by dynamically importing and registering if needed.

        Args:
            provider_type: Type identifier for the provider (e.g., 'aws', 'provider1')

        Returns:
            True if provider is registered (was already or just registered), False if failed
        """
        # Check if already registered
        if self.is_provider_registered(provider_type):
            if self._logger:
                self._logger.debug("Provider type '%s' already registered", provider_type)
            return True

        # Try to dynamically import and register
        if not _VALID_PROVIDER_TYPE_RE.match(provider_type):
            raise ValueError(f"Invalid provider type: {provider_type!r}")

        module_name = f"orb.providers.{provider_type}.registration"
        try:
            if self._logger:
                self._logger.debug("Attempting to register provider type: %s", provider_type)

            # Import the provider's registration module
            registration_module = importlib.import_module(module_name)

            # Call the provider's registration function
            register_function_name = f"register_{provider_type}_provider"
            if hasattr(registration_module, register_function_name):
                register_function = getattr(registration_module, register_function_name)
                register_function(self, self._logger)

                if self._logger:
                    self._logger.info("Successfully registered provider type: %s", provider_type)
                return True
            else:
                if self._logger:
                    self._logger.warning(
                        "Provider registration function '%s' not found in module '%s'",
                        register_function_name,
                        module_name,
                        exc_info=True,
                    )
                return False

        except ImportError as e:
            if self._logger:
                self._logger.warning(
                    "Failed to import provider registration module '%s': %s",
                    module_name,
                    e,
                    exc_info=True,
                )
            return False
        except Exception as e:
            if self._logger:
                self._logger.error(
                    "Error registering provider type '%s': %s", provider_type, e, exc_info=True
                )
            return False

    def ensure_provider_instance_registered_from_config(self, provider_instance: Any) -> bool:
        """
        Ensure provider instance is registered from config.
        Handles both type and instance registration.

        Args:
            provider_instance: ProviderInstanceConfig object

        Returns:
            True if registered successfully, False otherwise
        """
        # Already registered?
        if self.is_provider_instance_registered(provider_instance.name):
            if self._logger:
                self._logger.debug(
                    "Provider instance '%s' already registered", provider_instance.name
                )
            return True

        try:
            provider_type = provider_instance.type

            if not _VALID_PROVIDER_TYPE_RE.match(provider_type):
                raise ValueError(f"Invalid provider type: {provider_type!r}")

            if self._logger:
                self._logger.debug("Registering provider instance: %s", provider_instance.name)

            # Dynamically import provider registration module
            module = importlib.import_module(f"orb.providers.{provider_type}.registration")

            # Call provider's instance registration function
            register_func = getattr(module, f"register_{provider_type}_provider_instance")
            register_func(provider_instance, self._logger)

            if self._logger:
                self._logger.info(
                    "Successfully registered provider instance: %s", provider_instance.name
                )
            return True
        except (ImportError, AttributeError) as e:
            if self._logger:
                self._logger.warning(
                    f"Failed to register provider instance '{provider_instance.name}': {e}"
                )
            return False

    def register(  # type: ignore[override]
        self,
        provider_type: str,
        strategy_factory: Callable,
        config_factory: Callable,
        resolver_factory: Optional[Callable] = None,
        validator_factory: Optional[Callable] = None,
        strategy_class: Optional[type] = None,
        default_api: Optional[str] = None,
        display_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Register provider type - implements abstract method."""
        try:
            self.register_type(
                provider_type,
                strategy_factory,
                config_factory,
                resolver_factory=resolver_factory,
                validator_factory=validator_factory,
                strategy_class=strategy_class,
                default_api=default_api,
                display_name=display_name,
            )
        except ValueError as e:
            raise ConfigurationError(str(e))

    def get_display_name(self, provider_type: str) -> str:
        """Return the human-readable display name for *provider_type*.

        Falls back to the raw type identifier when the provider did not
        register one.

        Args:
            provider_type: Type identifier for the provider (e.g., 'aws')

        Returns:
            The registered display name, or *provider_type* itself when unset
            or the type is not registered.
        """
        try:
            registration = self._get_type_registration(provider_type)
        except ValueError:
            if self._logger:
                self._logger.debug(
                    "No display name registered for provider type %r; "
                    "falling back to the raw identifier.",
                    provider_type,
                )
            return provider_type
        if isinstance(registration, ProviderRegistration) and registration.display_name:
            return registration.display_name
        return provider_type

    def get_default_api(self, provider_type: str) -> Optional[str]:
        """Return the default API name for the given provider type, or None if not set.

        Args:
            provider_type: Type identifier for the provider (e.g., 'aws')

        Returns:
            Default API string from registration, or None if not registered / not set.
        """
        try:
            registration = self._get_type_registration(provider_type)
            if isinstance(registration, ProviderRegistration):
                return registration.default_api
        except (ValueError, KeyError):
            pass  # Unregistered provider type; fall through to the None default
        return None

    def register_provider(
        self,
        provider_type: str,
        strategy_factory: Callable,
        config_factory: Callable,
        resolver_factory: Optional[Callable] = None,
        validator_factory: Optional[Callable] = None,
        strategy_class: Optional[type] = None,
        default_api: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> None:
        """
        Register a provider with its factory functions - backward compatibility method.

        Args:
            provider_type: Type identifier for the provider (e.g., 'aws', 'provider1')
            strategy_factory: Factory function to create provider strategy
            config_factory: Factory function to create provider configuration
            resolver_factory: Optional factory for template resolver
            validator_factory: Optional factory for template validator
            strategy_class: Optional provider strategy class
            default_api: Optional default API name contributed by this provider
            display_name: Optional human-readable name for this provider

        Raises:
            ValueError: If provider_type is already registered
        """
        self.register(
            provider_type,
            strategy_factory,
            config_factory,
            resolver_factory,
            validator_factory,
            strategy_class=strategy_class,
            default_api=default_api,
            display_name=display_name,
        )

    def register_provider_instance(
        self,
        provider_type: str,
        instance_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        resolver_factory: Optional[Callable] = None,
        validator_factory: Optional[Callable] = None,
    ) -> None:
        """
        Register a named provider instance with its factory functions.

        Args:
            provider_type: Type identifier for the provider (e.g., 'aws')
            instance_name: Unique name for this provider instance (e.g., 'aws-us-east-1')
            strategy_factory: Factory function to create provider strategy
            config_factory: Factory function to create provider configuration
            resolver_factory: Optional factory for template resolver
            validator_factory: Optional factory for template validator

        Raises:
            ValueError: If instance_name is already registered
        """
        try:
            self.register_instance(
                provider_type,
                instance_name,
                strategy_factory,
                config_factory,
                resolver_factory=resolver_factory,
                validator_factory=validator_factory,
            )
        except ValueError:
            raise ValueError(f"Provider instance '{instance_name}' is already registered")

    def create_strategy(self, provider_type: str, config: Any = None) -> Any:  # type: ignore[override]
        """Create strategy - implements abstract method by delegating to cached method."""
        return self.get_or_create_strategy(provider_type, config)

    def create_config(self, provider_type: str, data: dict[str, Any]) -> Any:
        """
        Create a provider configuration using registered factory.

        Args:
            provider_type: Type identifier for the provider
            data: Configuration data dictionary

        Returns:
            Created provider configuration instance

        Raises:
            UnsupportedProviderError: If provider type is not registered
        """
        try:
            registration = self._get_type_registration(provider_type)
            config = registration.config_factory(data)
            if self._logger:
                self._logger.debug("Created config for provider: %s", provider_type)
            return config
        except ValueError:
            available_providers = ", ".join(self.get_registered_types())
            raise UnsupportedProviderError(
                f"Provider type '{provider_type}' is not registered. "
                f"Available providers: {available_providers}"
            )
        except Exception as e:
            raise ConfigurationError(
                f"Failed to create config for provider '{provider_type}': {e!s}"
            )

    def create_resolver(self, provider_type: str) -> Optional[Any]:
        """
        Create a template resolver using registered factory.

        Args:
            provider_type: Type identifier for the provider

        Returns:
            Created template resolver instance or None if not registered
        """
        return self.create_additional_component(provider_type, "resolver_factory")

    def create_validator(self, provider_type: str) -> Optional[Any]:
        """
        Create a template validator using registered factory.

        Args:
            provider_type: Type identifier for the provider

        Returns:
            Created template validator instance or None if not registered
        """
        return self.create_additional_component(provider_type, "validator_factory")

    def unregister_provider(self, provider_type: str) -> bool:
        """
        Unregister a provider - backward compatibility method.

        Args:
            provider_type: Type identifier for the provider

        Returns:
            True if provider was unregistered, False if not found
        """
        return self.unregister_type(provider_type)

    def unregister_provider_instance(self, instance_name: str) -> bool:
        """
        Unregister a named provider instance.

        Args:
            instance_name: Name of the provider instance

        Returns:
            True if instance was unregistered, False if not found
        """
        return self.unregister_instance(instance_name)

    def is_provider_registered(self, provider_type: str) -> bool:
        """
        Check if a provider type is registered - backward compatibility method.

        Args:
            provider_type: Type identifier for the provider

        Returns:
            True if provider is registered, False otherwise
        """
        return self.is_registered(provider_type)

    def is_provider_instance_registered(self, instance_name: str) -> bool:
        """
        Check if a provider instance is registered.

        Args:
            instance_name: Name of the provider instance

        Returns:
            True if instance is registered, False otherwise
        """
        return self.is_instance_registered(instance_name)

    def get_registered_providers(self) -> list[str]:
        """
        Get list of all registered provider types - backward compatibility method.

        Returns:
            List of registered provider type identifiers
        """
        return self.get_registered_types()

    def get_registered_provider_instances(self) -> list[str]:
        """
        Get list of all registered provider instance names.

        Returns:
            List of registered provider instance names
        """
        return self.get_registered_instances()

    def get_config_factory(self, provider_type: str) -> Optional[Any]:
        """Return the config_factory callable for the given provider type, or None if not registered."""
        try:
            registration = self._get_type_registration(provider_type)
            return getattr(registration, "config_factory", None)
        except (ValueError, KeyError):
            return None

    def list_all_provider_apis(self) -> list[str]:
        """Return a deduplicated list of all provider API names from registered strategies.

        Collects the ``get_supported_apis()`` result from each registered
        provider strategy class.  The list is sorted for stable output so
        callers do not depend on registration order.

        Used by the dashboard summary orchestrator to seed the
        ``by_provider_api`` zero-count keys without hard-coding provider names
        or API identifiers.
        """
        apis: list[str] = []
        for provider_type in self.get_registered_types():
            try:
                registration = self._get_type_registration(provider_type)
                strategy_class = getattr(registration, "strategy_class", None)
                if strategy_class is not None and hasattr(strategy_class, "get_supported_apis"):
                    # get_supported_apis may be an instance or classmethod depending
                    # on the provider — call it on a minimal stub when it is not a classmethod.
                    try:
                        supported = strategy_class.get_supported_apis(strategy_class)  # type: ignore[call-arg]
                    except TypeError:
                        supported = strategy_class.get_supported_apis()  # type: ignore[call-arg]
                    apis.extend(supported)
                elif hasattr(registration, "default_api") and getattr(
                    registration, "default_api", None
                ):
                    apis.append(getattr(registration, "default_api"))  # type: ignore[arg-type]
                else:
                    # Fall back to the provider type name itself as a minimal key.
                    apis.append(provider_type)
            except Exception:
                # A single provider failing to expose its APIs must not
                # block the caller: skip and continue collecting from the
                # remaining providers.
                continue
        seen: set[str] = set()
        return [a for a in sorted(apis) if not (a in seen or seen.add(a))]  # type: ignore[func-returns-value]

    def get_provider_instance_registration(
        self, instance_name: str
    ) -> Optional[ProviderRegistration]:
        """
        Get registration for a specific provider instance.

        Args:
            instance_name: Name of the provider instance

        Returns:
            ProviderRegistration if found, None otherwise
        """
        try:
            reg = self._get_instance_registration(instance_name)
            return reg if isinstance(reg, ProviderRegistration) else None
        except ValueError:
            return None

    def _provider_supports_capabilities(self, strategy: Any, capabilities: List[str]) -> bool:
        """Check if provider strategy supports required capabilities."""
        if not capabilities:
            return True

        provider_capabilities = getattr(strategy, "supported_capabilities", [])
        return all(cap in provider_capabilities for cap in capabilities)

    def _provider_supports_api(self, provider: Any, api: str) -> bool:
        """Thin delegator kept for backward-compatibility; logic lives in ProviderSelectionService."""
        return self._get_selection_service()._provider_supports_api(provider, api)

    # ============================================================================
    # PROVIDER SELECTION — delegated to ProviderSelectionService
    # ============================================================================
    # Selection policy (load-balancing, API-capability matching, etc.) now lives
    # in ProviderSelectionService (orb.infrastructure.services).  That service
    # depends only on ProviderRegistryPort and ConfigurationPort — both are domain
    # abstractions, so there is NO circular dependency.  The old "DO NOT MOVE"
    # comment was over-cautious: the concern was about moving to the *domain layer*,
    # not to a sibling infrastructure service.
    #
    # Bootstrap wires _selection_service after the registry singleton is created
    # (see bootstrap/provider_services.py).  In the rare case it is not yet wired
    # (e.g. tests that construct ProviderRegistry directly), the methods fall back
    # to a local ProviderSelectionService constructed on-demand.
    # ============================================================================

    def _get_selection_service(self) -> "ProviderSelectionService":
        """Return the injected selection service, constructing one lazily if needed."""
        if self._selection_service is None:
            from orb.infrastructure.services.provider_selection_service import (
                ProviderSelectionService,
            )

            self._selection_service = ProviderSelectionService(
                registry=self,
                config_port=self._config_port,  # type: ignore[arg-type]
            )
        return self._selection_service

    def select_provider_for_template(
        self, template: Any, provider_name: Optional[str] = None, logger: Optional[Any] = None
    ) -> ProviderSelectionResult:
        """Select provider instance for template requirements.

        Delegates to ProviderSelectionService — see that class for the full
        selection hierarchy.
        """
        return self._get_selection_service().select_provider_for_template(
            template, provider_name, logger
        )

    def select_active_provider(
        self,
        logger: Optional[Any] = None,
        *,
        provider_name: Optional[str] = None,
        provider_type: Optional[str] = None,
    ) -> ProviderSelectionResult:
        """Select active provider instance from configuration.

        Delegates to ProviderSelectionService — see that class for the full
        precedence rules.
        """
        return self._get_selection_service().select_active_provider(
            logger, provider_name=provider_name, provider_type=provider_type
        )

    def _create_registration(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **additional_factories: Any,
    ) -> BaseRegistration:
        """Create provider-specific registration."""
        return ProviderRegistration(
            type_name,
            strategy_factory,
            config_factory,
            additional_factories.get("resolver_factory"),
            additional_factories.get("validator_factory"),
            strategy_class=additional_factories.get("strategy_class"),
            default_api=additional_factories.get("default_api"),
            display_name=additional_factories.get("display_name"),
        )

    @staticmethod
    def _deep_merge(base: dict, update: dict) -> None:
        """Recursively merge update into base; arrays are replaced wholesale."""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                ProviderRegistry._deep_merge(base[key], value)
            else:
                base[key] = value

    def collect_defaults(self) -> dict:
        """Collect and merge defaults contributed by all registered provider strategies."""
        merged: dict = {}
        for reg in self._type_registrations.values():
            if isinstance(reg, ProviderRegistration) and reg.strategy_class is not None:
                try:
                    defaults = reg.strategy_class.get_defaults_config()
                    if defaults:
                        self._deep_merge(merged, defaults)
                except Exception as e:
                    self._logger.warning(
                        "Failed to collect defaults from %s: %s", reg.strategy_class, e
                    )
        return merged


# Global registry instance
_provider_registry_instance: Optional[ProviderRegistry] = None
_registry_lock = threading.Lock()


def get_provider_registry() -> ProviderRegistry:
    """Get the singleton provider registry instance."""
    global _provider_registry_instance

    if _provider_registry_instance is None:
        with _registry_lock:
            if _provider_registry_instance is None:
                _provider_registry_instance = ProviderRegistry()  # type: ignore[assignment]

    if _provider_registry_instance is None:
        raise RuntimeError("Provider registry not initialized")
    return _provider_registry_instance  # type: ignore[return-value]
