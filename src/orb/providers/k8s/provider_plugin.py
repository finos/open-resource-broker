"""Kubernetes provider plugin — structured onboarding via :class:`ProviderPlugin`.

Implements all mandatory satellite accessors by delegating to the existing
factory functions and class references in :mod:`orb.providers.k8s.registration`.
The legacy public functions in that module are kept as thin wrappers that
delegate to a module-level ``_k8s_plugin`` singleton so back-compat is preserved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.providers.base.provider_plugin import ProviderPlugin

if TYPE_CHECKING:
    pass


class K8sPlugin(ProviderPlugin):
    """Concrete :class:`ProviderPlugin` for the Kubernetes provider.

    All satellite accessors use lazy imports so this module imports cleanly even
    when the optional kubernetes SDK dependencies are absent.

    Args:
        inbound_auth_enabled: When ``True``, :meth:`register_auth_strategies`
            will register :class:`~orb.providers.k8s.auth.kube_auth_strategy.KubeAuthStrategy`
            with the ``AuthRegistry`` so ORB's REST surface can gate on caller
            ServiceAccount JWTs.  Mirrors the AWS provider pattern; default
            ``False`` because it requires a ``system:auth-delegator`` RBAC grant.
    """

    provider_name = "k8s"

    def __init__(self, inbound_auth_enabled: bool = False) -> None:
        self._inbound_auth_enabled = inbound_auth_enabled

    # ------------------------------------------------------------------
    # Mandatory satellite accessors
    # ------------------------------------------------------------------

    def strategy_factory(self) -> Any:
        from orb.providers.k8s.registration import create_k8s_strategy

        return create_k8s_strategy

    def config_factory(self) -> Any:
        from orb.providers.k8s.registration import create_k8s_config

        return create_k8s_config

    def resolver_factory(self) -> Optional[Any]:
        from orb.providers.k8s.registration import create_k8s_resolver

        return create_k8s_resolver

    def validator_factory(self) -> Optional[Any]:
        from orb.providers.k8s.registration import create_k8s_validator

        return create_k8s_validator

    def strategy_class(self) -> Optional[type]:
        try:
            from orb.providers.k8s.strategy.k8s_provider_strategy import (
                K8sProviderStrategy,
            )

            return K8sProviderStrategy
        except ImportError:
            return None

    def default_api(self) -> Optional[str]:
        return "Pod"

    def provider_settings_class(self) -> Optional[type]:
        try:
            from orb.providers.k8s.configuration.config import K8sProviderConfig

            return K8sProviderConfig
        except ImportError:
            return None

    def template_dto_config(self) -> Any:
        try:
            from orb.providers.k8s.domain.template.k8s_template_dto_config import (
                K8sTemplateDTOConfig,
            )

            return K8sTemplateDTOConfig
        except ImportError:
            return None

    def template_class(self) -> Optional[type]:
        try:
            from orb.providers.k8s.domain.template.k8s_template_aggregate import (
                K8sTemplate,
            )

            return K8sTemplate
        except ImportError:
            return None

    def cli_spec(self) -> Any:
        try:
            from orb.providers.k8s.cli.k8s_cli_spec import K8sCLISpec

            return K8sCLISpec()
        except ImportError:
            return None

    def field_mapping(self) -> Any:
        try:
            from orb.providers.k8s.scheduler.hostfactory_field_mapping import (
                K8sFieldMapping,
            )

            return K8sFieldMapping()
        except ImportError:
            return None

    def defaults_loader(self) -> Any:
        try:
            from orb.providers.k8s.defaults_loader import KubernetesDefaultsLoader

            return KubernetesDefaultsLoader()
        except ImportError:
            return None

    def template_example_generator(self, container: Any) -> Any:
        try:
            from orb.providers.k8s.adapters.template_example_generator_adapter import (
                create_k8s_template_example_generator,
            )

            return create_k8s_template_example_generator(container)
        except ImportError:
            return None

    # ------------------------------------------------------------------
    # Optional hook overrides
    # ------------------------------------------------------------------

    def register_auth_strategies(self, logger: Optional[Any] = None) -> None:
        """Register Kubernetes inbound HTTP auth strategies.

        Passes :attr:`_inbound_auth_enabled` through to
        :func:`~orb.providers.k8s.registration.register_k8s_auth_strategies`
        so the ``KubeAuthStrategy`` is only registered when the operator has
        explicitly opted in.
        """
        from orb.providers.k8s.registration import register_k8s_auth_strategies

        register_k8s_auth_strategies(logger, inbound_auth_enabled=self._inbound_auth_enabled)

    def _do_initialize(self, logger: Optional[Any] = None) -> None:
        """Register the Kubernetes retry classifier after DefaultsLoader.

        Runs after all standard satellite registrations complete.  Routes k8s
        non-retryable 4xx codes through the provider-agnostic registry so the
        resilience layer needs no direct kubernetes SDK import.
        """
        try:
            from orb.infrastructure.resilience.retry_classifier_registry import (
                register_retry_classifier,
            )
            from orb.providers.k8s.resilience.retry_classifier import (
                K8sRetryClassifier,
            )

            register_retry_classifier(K8sRetryClassifier())
        except ImportError as exc:
            # kubernetes extra not installed; skip retry-classifier registration silently.
            if logger:
                logger.debug(
                    "Skipping k8s retry-classifier registration — optional dependency absent: %s",
                    exc,
                )
            else:
                import logging as _logging

                _logging.getLogger(__name__).debug(
                    "Skipping k8s retry-classifier registration — optional dependency absent: %s",
                    exc,
                )

    def register_additional_services(self, container: Any, logger: Optional[Any] = None) -> None:
        """Register K8sTemplateAdapter and K8sNativeSpecService with the DI container.

        Delegates to the existing
        :func:`~orb.providers.k8s.registration.register_k8s_services_with_di`
        function so that the established ``suppress(ImportError)`` behavioural
        contract is preserved verbatim.  The template-example-generator is
        handled by :meth:`template_example_generator` so the base
        :meth:`register_services_with_di` does not double-register it.
        """
        from orb.domain.base.ports import LoggingPort
        from orb.domain.base.ports.template_adapter_port import TemplateAdapterPort
        from orb.providers.k8s.infrastructure.adapters.template_adapter import (
            K8sTemplateAdapter,
            create_k8s_template_adapter,
        )

        _logger = container.get(LoggingPort)

        container.register_singleton(K8sTemplateAdapter, create_k8s_template_adapter)
        container.register_singleton(TemplateAdapterPort, create_k8s_template_adapter)
        _logger.debug("Kubernetes Template Adapter registered with DI container")

        from contextlib import suppress

        with suppress(ImportError):
            from orb.domain.base.ports.configuration_port import ConfigurationPort
            from orb.providers.k8s.infrastructure.services.k8s_native_spec_service import (
                K8sNativeSpecService,
            )

            def _create_k8s_native_spec_service(_container) -> K8sNativeSpecService:
                from orb.application.services.native_spec_service import (
                    NativeSpecService,
                )

                return K8sNativeSpecService(
                    native_spec_service=_container.get(NativeSpecService),
                    config_port=_container.get(ConfigurationPort),
                )

            container.register_singleton(K8sNativeSpecService, _create_k8s_native_spec_service)
            _logger.debug("K8sNativeSpecService registered with DI container")


# ---------------------------------------------------------------------------
# Module-level singleton used by the thin-wrapper backwards-compat functions
# in orb.providers.k8s.registration.
# ---------------------------------------------------------------------------
