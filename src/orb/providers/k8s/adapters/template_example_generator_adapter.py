"""Infrastructure adapter implementing TemplateExampleGeneratorPort via K8sHandlerRegistry.

Mirrors :mod:`orb.providers.aws.adapters.template_example_generator_adapter` —
delegates to the same handler table that drives live ``acquire`` dispatch so the
handler list has a single source of truth.  Adding or removing a handler
means editing the strategy's ``_HANDLER_CLASSES`` only.

Registered into
:class:`~orb.infrastructure.registry.template_example_generator_registry.TemplateExampleGeneratorRegistry`
under the key ``"k8s"`` during provider bootstrap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort


class KubernetesTemplateExampleGeneratorAdapter:
    """Generates example templates by delegating to :class:`K8sHandlerRegistry`.

    Implements :class:`~orb.domain.base.ports.template_example_generator_port.TemplateExampleGeneratorPort`
    (structural Protocol).  The ``provider_type`` discriminator argument has
    been removed — this adapter is only ever called for the ``"k8s"`` provider
    type, which is enforced by the registry layer.
    """

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        strategy_class: Optional[type] = None,
    ) -> None:
        self._logger = logger
        # The strategy class whose handler table supplies the examples; a
        # provider type built on the Kubernetes provider passes its own.
        self._strategy_class = strategy_class

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
        handler_classes, plugin_factories = _resolve_handler_tables(self._strategy_class)
        examples = K8sHandlerRegistry.generate_example_templates(
            plugin_factories=plugin_factories, handler_classes=handler_classes
        )

        if provider_api:
            examples = [t for t in examples if getattr(t, "provider_api", None) == provider_api]

        return examples


def _resolve_handler_tables(
    strategy_class: Optional[type],
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Return ``(handler classes, plugin factories)`` declared on the strategy class.

    Read from the class so no live strategy instance is needed.  Best-effort:
    if the strategy module fails to import the built-in handler table and no
    plugin factories are used, so the built-in examples still surface.
    """
    if strategy_class is None:
        try:
            from orb.providers.k8s.strategy.k8s_provider_strategy import (
                K8sProviderStrategy,
            )
        except Exception:
            return None, {}
        strategy_class = K8sProviderStrategy
    # getattr: a class passed here may predate the hook and not declare the table
    handler_classes = getattr(strategy_class, "_HANDLER_CLASSES", None)
    # getattr: a class passed here may predate the hook and not declare the table
    factories = getattr(strategy_class, "_DEFAULT_HANDLER_FACTORIES", None) or {}
    return (dict(handler_classes) if handler_classes is not None else None), dict(factories)


def create_k8s_template_example_generator(
    container: Any,
    strategy_class: Optional[type] = None,
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
    except Exception:
        # LoggingPort is not required for example-template generation.  The
        # caller falls back to a stdlib logger when adapter.logger is None,
        # so a container miss (test harness, minimal DI setup) is safe to
        # swallow silently.
        logger = None
    return KubernetesTemplateExampleGeneratorAdapter(logger=logger, strategy_class=strategy_class)
