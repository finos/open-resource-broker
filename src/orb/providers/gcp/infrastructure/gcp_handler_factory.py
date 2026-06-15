"""Factory for GCP runtime handlers."""

from __future__ import annotations

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.domain.template.value_objects import GCPProviderApi
from orb.providers.gcp.exceptions import GCPValidationError
from orb.providers.gcp.infrastructure.compute_client import GCPComputeClient
from orb.providers.gcp.infrastructure.handlers.base_handler import GCPHandler


@injectable
class GCPHandlerFactory:
    """Create and cache GCP handlers keyed by provider API."""

    def __init__(
        self,
        compute_client: GCPComputeClient,
        config: GCPProviderConfig,
        logger: LoggingPort,
    ) -> None:
        """Initialize the handler cache and register supported handler classes."""
        self._compute_client = compute_client
        self._config = config
        self._logger = logger
        self._handlers: dict[GCPProviderApi, GCPHandler] = {}
        self._handler_classes: dict[GCPProviderApi, type[GCPHandler]] = {}
        self._register_handler_classes()

    @staticmethod
    def _normalize_handler_type(handler_type: GCPProviderApi | str) -> GCPProviderApi:
        """Normalize string or enum handler input to the canonical enum."""
        if isinstance(handler_type, GCPProviderApi):
            return handler_type
        return GCPProviderApi(handler_type)

    def create_handler(self, handler_type: GCPProviderApi | str) -> GCPHandler:
        """Return a cached handler instance for the requested GCP API."""
        try:
            handler_key = self._normalize_handler_type(handler_type)
        except ValueError as exc:
            raise GCPValidationError(f"Invalid GCP handler type: {handler_type}") from exc

        if handler_key in self._handlers:
            return self._handlers[handler_key]

        handler_class = self._handler_classes.get(handler_key)
        if handler_class is None:
            raise GCPValidationError(f"No handler class registered for type: {handler_key.value}")

        handler = handler_class(
            compute_client=self._compute_client,
            config=self._config,
            logger=self._logger,
        )
        self._handlers[handler_key] = handler
        return handler

    def _register_handler_classes(self) -> None:
        from orb.providers.gcp.infrastructure.handlers.mig_handler import (
            GCPManagedInstanceGroupHandler,
        )
        from orb.providers.gcp.infrastructure.handlers.single_vm_handler import (
            GCPSingleVMHandler,
        )

        self._handler_classes = {
            GCPProviderApi.MIG: GCPManagedInstanceGroupHandler,
            GCPProviderApi.SINGLE_VM: GCPSingleVMHandler,
        }
