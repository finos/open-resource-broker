"""Infrastructure adapter implementing TemplateExampleGeneratorPort via K8sHandlerRegistry.

Mirrors :mod:`orb.providers.aws.adapters.template_example_generator_adapter` —
delegates to the same registry that drives live ``acquire`` dispatch so the
handler list has a single source of truth.  Adding or removing a handler
means editing :attr:`K8sHandlerRegistry._HANDLER_CLASSES` only.

Registered into
:class:`~orb.infrastructure.registry.template_example_generator_registry.TemplateExampleGeneratorRegistry`
under the key ``"k8s"`` during provider bootstrap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.domain.base.ports import LoggingPort


class KubernetesTemplateExampleGeneratorAdapter:
    """Generates example templates by delegating to :class:`K8sHandlerRegistry`.

    Implements :class:`~orb.domain.base.ports.template_example_generator_port.TemplateExampleGeneratorPort`
    (structural Protocol).  The ``provider_type`` discriminator argument has
    been removed — this adapter is only ever called for the ``"k8s"`` provider
    type, which is enforced by the registry layer.
    """

    def __init__(self, logger: Optional[LoggingPort] = None) -> None:
        self._logger = logger

    def generate_example_templates(
        self,
        provider_name: str,
        provider_api: Optional[str] = None,
    ) -> list[Any]:
        """Return example templates for the Kubernetes provider.

        Args:
            provider_name: Ignored — used only for API parity with the port.
            provider_api: When given, only examples whose ``provider_api``
                matches are returned (e.g. ``"Pod"``, ``"Deployment"``).

        Returns:
            List of :class:`~orb.providers.k8s.domain.template.k8s_template_aggregate.K8sTemplate`
            instances.
        """
        plugin_factories = _resolve_plugin_factories()
        examples = K8sHandlerRegistry.generate_example_templates(plugin_factories=plugin_factories)

        if provider_api:
            examples = [t for t in examples if getattr(t, "provider_api", None) == provider_api]

        return examples


def _resolve_plugin_factories() -> dict[str, Any]:
    """Return the plugin-registered handler factories, or an empty dict.

    Reads :attr:`K8sProviderStrategy._HANDLER_FACTORIES` directly so we
    do not need a live strategy instance.  Best-effort: if the strategy
    module fails to import we return an empty dict so the built-in
    handlers still surface their examples.
    """
    try:
        from orb.providers.k8s.strategy.k8s_provider_strategy import (
            K8sProviderStrategy,
        )
    except Exception:
        return {}
    return dict(getattr(K8sProviderStrategy, "_HANDLER_FACTORIES", {}) or {})


def create_k8s_template_example_generator(
    container: Any,
) -> KubernetesTemplateExampleGeneratorAdapter:
    """Factory — construct :class:`KubernetesTemplateExampleGeneratorAdapter`.

    Resolves ``LoggingPort`` from the container and injects it so the adapter
    can log import errors at debug level.  The resolution is best-effort so
    the factory is safe to call before the logger is registered.
    """
    logger: Optional[LoggingPort] = None
    try:
        from orb.domain.base.ports import LoggingPort as _LoggingPort

        logger = container.get(_LoggingPort)
    except Exception:  # noqa: BLE001 — DI lookup is best-effort; fall back to no-logger
        # LoggingPort is not required for example-template generation.  The
        # caller falls back to a stdlib logger when adapter.logger is None,
        # so a container miss (test harness, minimal DI setup) is safe to
        # swallow silently.
        logger = None
    return KubernetesTemplateExampleGeneratorAdapter(logger=logger)
