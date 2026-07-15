"""Azure Handler Factory.

Creates and caches Azure handlers based on ``provider_api`` values
"""

from threading import RLock
from typing import TYPE_CHECKING

from orb.domain.base.ports import LoggingPort
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.di.injectable import injectable
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions.azure_exceptions import AzureValidationError
from orb.providers.azure.infrastructure.azure_client import AzureClient
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureHandler

if TYPE_CHECKING:
    from orb.providers.azure.infrastructure.services.azure_native_spec_service import (
        AzureNativeSpecService,
    )
    from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager


@injectable
class AzureHandlerFactory:
    """Factory for creating Azure handlers keyed by ``AzureProviderApi``."""

    def __init__(
        self,
        azure_client: AzureClient,
        logger: LoggingPort,
        azure_native_spec_service: "AzureNativeSpecService | None" = None,
        azure_resource_manager: "AzureResourceManager | None" = None,
    ) -> None:
        """Initialize the factory with an Azure client and register handler classes."""
        self._azure_client = azure_client
        self._logger = logger
        self._azure_native_spec_service = azure_native_spec_service
        self._azure_resource_manager = azure_resource_manager
        self._lock = RLock()
        self._handlers: dict[AzureProviderApi, AzureHandler] = {}
        self._handler_classes: dict[AzureProviderApi, type[AzureHandler]] = {}
        self._register_handler_classes()

    @property
    def azure_client(self) -> AzureClient:
        """Return the underlying Azure client instance."""
        return self._azure_client

    @staticmethod
    def _normalize_handler_type(handler_type: AzureProviderApi | str) -> AzureProviderApi:
        if isinstance(handler_type, AzureProviderApi):
            return handler_type
        return AzureProviderApi(handler_type)

    def create_handler(self, handler_type: AzureProviderApi | str) -> AzureHandler:
        """Create (or return cached) handler for *handler_type*.

        Raises:
            AzureValidationError: If *handler_type* is unknown.
        """
        try:
            handler_type_key = self._normalize_handler_type(handler_type)
        except ValueError:
            raise AzureValidationError(f"Invalid Azure handler type: {handler_type}")
        with self._lock:
            if handler_type_key in self._handlers:
                return self._handlers[handler_type_key]

            if handler_type_key not in self._handler_classes:
                raise AzureValidationError(
                    f"No handler class registered for type: {handler_type_key.value}"
                )

            if handler_type_key is AzureProviderApi.SINGLE_VM:
                from orb.providers.azure.infrastructure.handlers.single_vm_handler import (
                    SingleVMHandler,
                )

                handler = SingleVMHandler(
                    azure_client=self._azure_client,
                    logger=self._logger,
                    azure_native_spec_service=self._azure_native_spec_service,
                )
            elif handler_type_key in (AzureProviderApi.VMSS, AzureProviderApi.VMSS_UNIFORM):
                from orb.providers.azure.infrastructure.handlers.vmss_handler import VMSSHandler

                handler = VMSSHandler(
                    azure_client=self._azure_client,
                    logger=self._logger,
                    azure_native_spec_service=self._azure_native_spec_service,
                    azure_resource_manager=self._azure_resource_manager,
                )
            else:
                from orb.providers.azure.infrastructure.handlers.cyclecloud_handler import (
                    CycleCloudHandler,
                )

                handler = CycleCloudHandler(
                    azure_client=self._azure_client,
                    logger=self._logger,
                )
            self._handlers[handler_type_key] = handler
            self._logger.debug("Created Azure handler for type: %s", handler_type_key.value)
            return handler

    def create_handler_for_template(self, template: Template) -> AzureHandler:
        """Create handler appropriate for *template.provider_api*."""
        handler_type = template.provider_api or AzureProviderApi.VMSS
        return self.create_handler(handler_type)

    def _register_handler_classes(self) -> None:
        self._handler_classes = self.get_handler_classes()
        self._logger.debug(
            "Registered Azure handler classes: %s",
            [handler_type.value for handler_type in self._handler_classes],
        )

    @staticmethod
    def get_handler_classes() -> dict[AzureProviderApi, type[AzureHandler]]:
        """Return the handler classes that define Azure's supported APIs."""
        from orb.providers.azure.infrastructure.handlers.cyclecloud_handler import (
            CycleCloudHandler,
        )
        from orb.providers.azure.infrastructure.handlers.single_vm_handler import (
            SingleVMHandler,
        )
        from orb.providers.azure.infrastructure.handlers.vmss_handler import VMSSHandler

        return {
            AzureProviderApi.VMSS: VMSSHandler,
            AzureProviderApi.VMSS_UNIFORM: VMSSHandler,
            AzureProviderApi.SINGLE_VM: SingleVMHandler,
            AzureProviderApi.CYCLECLOUD: CycleCloudHandler,
        }

    def registered_handler_types(self) -> tuple[AzureProviderApi, ...]:
        """Return the registered handler enums in factory-owned canonical order."""
        return tuple(self._handler_classes.keys())

    def get_all_handlers(self) -> dict[str, AzureHandler]:
        """Materialize and return handlers keyed by serialized Azure provider API values."""
        return {
            handler_type.value: self.create_handler(handler_type)
            for handler_type in self.registered_handler_types()
        }

    def generate_example_templates(self) -> list[dict]:
        """Collect example templates from all registered handlers."""
        return generate_azure_example_templates()


def generate_azure_example_templates() -> list[dict[str, object]]:
    """Generate examples from the same handler set used for live dispatch."""
    examples: list[dict[str, object]] = []
    for handler_class in AzureHandlerFactory.get_handler_classes().values():
        examples.extend(handler_class.get_example_templates())
    return examples
