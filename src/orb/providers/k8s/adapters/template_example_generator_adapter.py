"""Infrastructure adapter implementing TemplateExampleGeneratorPort for the Kubernetes provider.

Mirrors :mod:`orb.providers.aws.adapters.template_example_generator_adapter` — collects
example templates from every concrete handler class and exposes them through the
:class:`~orb.domain.base.ports.template_example_generator_port.TemplateExampleGeneratorPort`
so ``orb templates list --provider k8s`` produces results.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base.ports.template_example_generator_port import TemplateExampleGeneratorPort

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.domain.base.ports import LoggingPort

# Concrete handler classes that ship example templates.
_HANDLER_CLASSES = [
    "orb.providers.k8s.handlers.pod_handler:K8sPodHandler",
    "orb.providers.k8s.handlers.deployment_handler:K8sDeploymentHandler",
    "orb.providers.k8s.handlers.statefulset_handler:K8sStatefulSetHandler",
    "orb.providers.k8s.handlers.job_handler:K8sJobHandler",
]


def _collect_handler_examples() -> list[Any]:
    """Return example templates from all registered handler classes.

    Iterates ``_HANDLER_CLASSES``, imports each one defensively, and calls
    :meth:`get_example_templates` on the class object.  Import errors are
    silenced so the function degrades gracefully when optional handler modules
    are unavailable in a given deployment.
    """
    import importlib

    templates: list[Any] = []
    for ref in _HANDLER_CLASSES:
        module_path, class_name = ref.split(":")
        try:
            mod = importlib.import_module(module_path)
            handler_cls = getattr(mod, class_name)
            templates.extend(handler_cls.get_example_templates())
        except Exception:  # noqa: BLE001 — degrade gracefully
            pass
    return templates


class KubernetesTemplateExampleGeneratorAdapter(TemplateExampleGeneratorPort):
    """Generates example templates by calling each handler's ``get_example_templates``."""

    def __init__(self, logger: Optional["LoggingPort"] = None) -> None:
        self._logger = logger

    def generate_example_templates(
        self,
        provider_type: str,
        provider_name: str,
        provider_api: Optional[str] = None,
    ) -> list[Any]:
        """Return example templates for the Kubernetes provider.

        Args:
            provider_type: Must be ``"k8s"``; returns an empty list for any
                other value so the adapter is safe to call unconditionally.
            provider_name: Ignored — used only for API parity with the port.
            provider_api: When given, only examples whose ``provider_api``
                matches are returned (e.g. ``"Pod"``, ``"Deployment"``).

        Returns:
            List of :class:`~orb.providers.k8s.domain.template.k8s_template.K8sTemplate`
            instances (or empty list when ``provider_type != "k8s"``).
        """
        if provider_type != "k8s":
            return []

        examples = _collect_handler_examples()

        if provider_api:
            examples = [t for t in examples if getattr(t, "provider_api", None) == provider_api]

        return examples


def create_k8s_template_example_generator(
    container: Any,
) -> KubernetesTemplateExampleGeneratorAdapter:
    """DI factory — construct :class:`KubernetesTemplateExampleGeneratorAdapter`.

    Resolves ``LoggingPort`` from the container and injects it so the adapter
    can log import errors at debug level.  The resolution is best-effort so
    the factory is safe to call before the logger is registered.
    """
    logger: Optional[LoggingPort] = None
    try:
        from orb.domain.base.ports import LoggingPort as _LoggingPort

        logger = container.get(_LoggingPort)
    except Exception:  # noqa: BLE001
        pass
    return KubernetesTemplateExampleGeneratorAdapter(logger=logger)
