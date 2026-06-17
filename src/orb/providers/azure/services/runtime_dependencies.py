"""Azure runtime-owned lazy dependency resolution."""

from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.configuration.config import AzureProviderConfig

if TYPE_CHECKING:
    from orb.providers.azure.infrastructure.azure_client import AzureClient
    from orb.providers.azure.infrastructure.azure_handler_factory import AzureHandlerFactory
    from orb.providers.azure.infrastructure.services.azure_deployment_service import (
        AzureDeploymentService,
    )
    from orb.providers.azure.infrastructure.services.azure_native_spec_service import (
        AzureNativeSpecService,
    )
    from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager


class AzureRuntimeDependencies:
    """Own Azure lazy runtime dependency resolution and cache lifecycle."""

    def __init__(
        self,
        *,
        config: AzureProviderConfig,
        logger: LoggingPort,
        azure_client_resolver: Optional[Callable[[], AzureClient]] = None,
        azure_handler_factory_resolver: Optional[Callable[[], AzureHandlerFactory]] = None,
        azure_resource_manager_resolver: Optional[Callable[[], AzureResourceManager | None]] = None,
        azure_deployment_service_resolver: Optional[
            Callable[[], AzureDeploymentService | None]
        ] = None,
        azure_native_spec_service: Optional[AzureNativeSpecService] = None,
    ) -> None:
        """Initialize the Azure runtime dependency holder and its lazy resolvers."""
        self._config = config
        self._logger = logger
        self._azure_client_resolver = azure_client_resolver
        self._azure_handler_factory_resolver = azure_handler_factory_resolver
        self._azure_resource_manager_resolver = azure_resource_manager_resolver
        self._azure_deployment_service_resolver = azure_deployment_service_resolver
        self._azure_native_spec_service = azure_native_spec_service
        self._lock = RLock()
        self._client: Optional[AzureClient] = None
        self._resource_manager: Optional[AzureResourceManager] = None
        self._deployment_service: Optional[AzureDeploymentService] = None
        self._handler_factory: Optional[AzureHandlerFactory] = None

    @property
    def azure_client(self) -> Optional[AzureClient]:
        """Resolve and cache AzureClient on first access."""
        with self._lock:
            if self._client is None:
                self._logger.debug("Creating Azure client on first access")
                if self._azure_client_resolver:
                    try:
                        self._client = self._azure_client_resolver()
                    except Exception as exc:
                        self._logger.warning(
                            "Failed to resolve AzureClient lazily: %s",
                            exc,
                            exc_info=True,
                        )
                        self._client = None
                else:
                    self._logger.warning("AzureClient resolver not provided")
            return self._client

    @property
    def resource_manager(self) -> Optional[AzureResourceManager]:
        """Resolve and cache AzureResourceManager on first access."""
        with self._lock:
            azure_client = self.azure_client
            if self._resource_manager is None and self._azure_resource_manager_resolver is not None:
                try:
                    self._resource_manager = self._azure_resource_manager_resolver()
                except Exception as exc:
                    self._logger.warning(
                        "Failed to resolve AzureResourceManager lazily: %s",
                        exc,
                        exc_info=True,
                    )
                    self._resource_manager = None
            if self._resource_manager is None and azure_client:
                self._logger.debug("Creating Azure resource manager on first access")
                from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager

                self._resource_manager = AzureResourceManager(
                    azure_client=azure_client,
                    config=self._config,
                    logger=self._logger,
                )
            return self._resource_manager

    @property
    def deployment_service(self) -> Optional[AzureDeploymentService]:
        """Resolve and cache AzureDeploymentService on first access."""
        with self._lock:
            azure_client = self.azure_client
            if self._deployment_service is None and self._azure_deployment_service_resolver is not None:
                try:
                    self._deployment_service = self._azure_deployment_service_resolver()
                except Exception as exc:
                    self._logger.warning(
                        "Failed to resolve AzureDeploymentService lazily: %s",
                        exc,
                        exc_info=True,
                    )
                    self._deployment_service = None
            if self._deployment_service is None and azure_client:
                from orb.providers.azure.infrastructure.services.azure_deployment_service import (
                    AzureDeploymentService,
                )

                self._deployment_service = AzureDeploymentService(
                    azure_client=azure_client,
                    logger=self._logger,
                )
            return self._deployment_service

    @property
    def handler_factory(self) -> Optional[AzureHandlerFactory]:
        """Resolve and cache AzureHandlerFactory on first access."""
        with self._lock:
            if self._handler_factory is not None:
                return self._handler_factory

            if self._azure_handler_factory_resolver is not None:
                try:
                    self._handler_factory = self._azure_handler_factory_resolver()
                    return self._handler_factory
                except Exception as exc:
                    self._logger.warning(
                        "Failed to resolve AzureHandlerFactory lazily: %s",
                        exc,
                        exc_info=True,
                    )
                    self._handler_factory = None
                    return None

            azure_client = self.azure_client
            if azure_client is None:
                return None

            from orb.providers.azure.infrastructure.azure_handler_factory import (
                AzureHandlerFactory,
            )

            self._handler_factory = AzureHandlerFactory(
                azure_client=azure_client,
                logger=self._logger,
                azure_native_spec_service=self._azure_native_spec_service,
                azure_resource_manager=self.resource_manager,
            )
            return self._handler_factory

    def clear_cached_runtime(self) -> Optional[AzureClient]:
        """Clear all cached runtime dependencies and return the cached client."""
        with self._lock:
            client = self._client
            self._client = None
            self._resource_manager = None
            self._deployment_service = None
            self._handler_factory = None
            return client
