"""Native-spec DI plumbing for the Kubernetes provider.

Extracted from :class:`orb.providers.k8s.strategy.k8s_provider_strategy.
K8sProviderStrategy`.  Wraps the raw ``NativeSpecService`` from the
application layer in a :class:`K8sNativeSpecService` on first handler build,
gated on config + wired ``ConfigurationPort`` + an injected service.

The strategy delegates :meth:`resolve` from its
``_resolve_native_spec_service`` accessor (which the handler registry calls
via a provider closure) so the DI-resolution contract is unchanged.
"""

from __future__ import annotations

from typing import Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.providers.k8s.configuration.config import K8sProviderConfig


class K8sNativeSpecResolver:
    """Lazily resolve :class:`K8sNativeSpecService` once per strategy instance."""

    def __init__(
        self,
        *,
        config: K8sProviderConfig,
        logger: LoggingPort,
        config_port: Optional[ConfigurationPort],
        injected_native_spec_service: Optional[Any],
    ) -> None:
        self._config = config
        self._logger = logger
        self._config_port = config_port
        # Native-spec escape hatch.  Resolved lazily on first handler
        # construction.  ``None`` after resolution means the service is
        # unavailable (jinja2 missing, injected service not provided, etc.)
        # — handlers fall back to the typed builder path.  The injected
        # ``native_spec_service`` is the raw ``NativeSpecService`` from the
        # application layer; :meth:`resolve` wraps it in
        # ``K8sNativeSpecService`` on first call.  There is no DI container
        # fallback — callers that need native-spec support must supply the
        # service via the strategy constructor.
        self._injected_native_spec_service: Optional[Any] = injected_native_spec_service
        self._resolved: bool = False
        self._k8s_native_spec_service: Optional[Any] = None

    def resolve(self) -> Optional[Any]:
        """Resolve :class:`K8sNativeSpecService` once on first handler build.

        Returns ``None`` when the provider config opts out
        (``native_spec_enabled=False``), when no ``ConfigurationPort`` is
        wired, or when no ``native_spec_service`` was passed at construction
        time.  All construction paths that need native-spec support must
        supply the service via the constructor parameter — the
        :func:`orb.providers.k8s.registration.create_k8s_strategy` factory
        resolves it from the DI container at strategy-creation time and
        passes it explicitly.  There is no ``get_container()`` fallback.
        """
        if self._resolved:
            return self._k8s_native_spec_service
        self._resolved = True

        if not self._config.native_spec_enabled:
            return None

        if self._config_port is None:
            self._logger.debug(
                "Kubernetes native-spec service unavailable: no ConfigurationPort "
                "wired into the strategy (typed builder path will be used)."
            )
            return None

        if self._injected_native_spec_service is None:
            self._logger.debug(
                "Kubernetes native-spec service unavailable: no NativeSpecService "
                "injected at construction time (typed builder path will be used).  "
                "Ensure create_k8s_strategy is used so the service is resolved from "
                "the DI container before strategy construction."
            )
            return None

        try:
            from orb.providers.k8s.infrastructure.services.k8s_native_spec_service import (
                K8sNativeSpecService,
            )

            self._k8s_native_spec_service = K8sNativeSpecService(
                native_spec_service=self._injected_native_spec_service,
                config_port=self._config_port,
                k8s_config=self._config,
            )
            return self._k8s_native_spec_service
        except Exception as exc:
            self._logger.warning(
                "K8sNativeSpecService unavailable, native spec enrichment disabled: %s",
                exc,
            )
            return None
