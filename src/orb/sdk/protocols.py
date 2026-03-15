"""Typed protocol for SDK business methods.

Provides IDE autocompletion and type checking for dynamically
discovered CQRS methods that are attached via setattr at runtime.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ORBClientProtocol(Protocol):
    """Protocol defining the public SDK method surface.

    These methods are discovered from CQRS handlers at runtime and
    attached via setattr. This protocol exists purely for type checking
    and IDE support — it does not affect runtime behaviour.

    Note on collisions: where a query and command share the same derived
    name (e.g. convert_machine_status), the command handler wins because
    commands are discovered after queries in SDKMethodDiscovery.
    """

    # --- Template operations ---
    async def get_template(self, *, template_id: str, **kwargs: Any) -> dict[str, Any]:
        pass

    async def list_templates(
        self, *, active_only: bool = False, **kwargs: Any
    ) -> list[dict[str, Any]]:
        pass

    async def validate_template(self, *, template_id: str, **kwargs: Any) -> dict[str, Any]:
        pass

    async def get_configuration(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def create_template(self, *, template_id: str, **kwargs: Any) -> None:
        pass

    async def update_template(self, *, template_id: str, **kwargs: Any) -> None:
        pass

    async def delete_template(self, *, template_id: str, **kwargs: Any) -> None:
        pass

    async def refresh_templates(self, **kwargs: Any) -> None:
        pass

    # --- Request operations ---
    async def get_request(self, *, request_id: str, **kwargs: Any) -> dict[str, Any]:
        pass

    async def list_requests(self, **kwargs: Any) -> list[dict[str, Any]]:
        pass

    async def list_return_requests(self, **kwargs: Any) -> list[dict[str, Any]]:
        pass

    async def list_active_requests(self, **kwargs: Any) -> list[dict[str, Any]]:
        pass

    async def get_request_summary(self, *, request_id: str, **kwargs: Any) -> dict[str, Any]:
        pass

    async def create_request(
        self, *, template_id: str, count: int = 1, **kwargs: Any
    ) -> dict[str, Any]:
        pass

    async def create_return_request(
        self, *, machine_ids: list[str], **kwargs: Any
    ) -> dict[str, Any]:
        pass

    async def update_request_status(self, *, request_id: str, **kwargs: Any) -> None:
        pass

    async def cancel_request(self, *, request_id: str, **kwargs: Any) -> None:
        pass

    async def complete_request(self, *, request_id: str, **kwargs: Any) -> None:
        pass

    async def sync_request(self, *, request_id: str, **kwargs: Any) -> None:
        pass

    async def populate_machine_ids(self, *, request_id: str, **kwargs: Any) -> None:
        pass

    async def wait_for_request(
        self,
        request_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 10.0,
    ) -> dict[str, Any]:
        pass

    async def wait_for_return(
        self,
        return_request_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 10.0,
    ) -> dict[str, Any]:
        pass

    # --- Machine operations ---
    async def get_machine(self, *, machine_id: str, **kwargs: Any) -> dict[str, Any]:
        pass

    async def list_machines(self, **kwargs: Any) -> list[dict[str, Any]]:
        pass

    async def get_active_machine_count(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def get_machine_health(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def update_machine_status(self, *, machine_id: str, **kwargs: Any) -> None:
        pass

    # convert_machine_status, convert_batch_machine_status, validate_provider_state:
    # command wins over query of the same name at runtime
    async def convert_machine_status(self, **kwargs: Any) -> None:
        pass

    async def convert_batch_machine_status(self, **kwargs: Any) -> None:
        pass

    async def cleanup_machine_resources(self, **kwargs: Any) -> None:
        pass

    async def register_machine(self, **kwargs: Any) -> None:
        pass

    async def deregister_machine(self, *, machine_id: str, **kwargs: Any) -> None:
        pass

    # --- Provider operations ---
    async def get_provider_health(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def list_available_providers(self, **kwargs: Any) -> list[dict[str, Any]]:
        pass

    async def get_provider_capabilities(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def get_provider_metrics(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def get_provider_strategy_config(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def execute_provider_operation(self, **kwargs: Any) -> None:
        pass

    async def register_provider_strategy(self, **kwargs: Any) -> None:
        pass

    async def update_provider_health(self, **kwargs: Any) -> None:
        pass

    # --- Bulk operations ---
    async def get_multiple_requests(
        self, *, request_ids: list[str], **kwargs: Any
    ) -> list[dict[str, Any]]:
        pass

    async def get_multiple_templates(
        self, *, template_ids: list[str], **kwargs: Any
    ) -> list[dict[str, Any]]:
        pass

    async def get_multiple_machines(
        self, *, machine_ids: list[str], **kwargs: Any
    ) -> list[dict[str, Any]]:
        pass

    # --- Cleanup operations ---
    async def list_cleanable_requests(self, **kwargs: Any) -> list[dict[str, Any]]:
        pass

    async def list_cleanable_resources(self, **kwargs: Any) -> list[dict[str, Any]]:
        pass

    async def cleanup_old_requests(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def cleanup_all_resources(self, **kwargs: Any) -> dict[str, Any]:
        pass

    # --- Storage operations ---
    async def list_storage_strategies(self, **kwargs: Any) -> list[dict[str, Any]]:
        pass

    async def get_storage_health(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def get_storage_metrics(self, **kwargs: Any) -> dict[str, Any]:
        pass

    # --- Scheduler operations ---
    async def list_scheduler_strategies(self, **kwargs: Any) -> list[dict[str, Any]]:
        pass

    async def get_scheduler_configuration(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def validate_scheduler_configuration(self, **kwargs: Any) -> dict[str, Any]:
        pass

    # --- System / config operations ---
    async def get_configuration_section(self, *, section: str, **kwargs: Any) -> dict[str, Any]:
        pass

    async def get_provider_config(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def validate_provider_config(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def get_system_status(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def validate_storage(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def validate_mcp(self, **kwargs: Any) -> dict[str, Any]:
        pass

    async def validate_provider_state(self, **kwargs: Any) -> None:
        pass

    async def reload_provider_config(self, **kwargs: Any) -> None:
        pass

    async def set_configuration(self, **kwargs: Any) -> None:
        pass
