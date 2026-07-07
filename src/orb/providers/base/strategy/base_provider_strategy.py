"""Base provider strategy implementing ProviderPort."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from orb.domain.base.operation_outcome import OperationOutcome
from orb.domain.base.ports.provider_port import ProviderPort

if TYPE_CHECKING:
    from orb.domain.request.aggregate import Request


class BaseProviderStrategy(ProviderPort, ABC):
    """Base class for all provider strategies implementing ProviderPort."""

    def __init__(self, config: dict[str, Any], logger: Any) -> None:
        """Initialize base provider strategy.

        Args:
            config: Provider configuration
            logger: Logger instance
        """
        self.config = config
        self.logger = logger

    def get_provider_info(self) -> dict[str, Any]:
        """Get provider information."""
        return {"type": self.__class__.__name__, "config": self.config}

    @classmethod
    def get_defaults_config(cls) -> dict:
        return {}

    @classmethod
    def is_image_resolution_needed(cls) -> bool:
        """Return True when this provider requires SSM / image-ID resolution.

        The default is ``False``.  Override to ``True`` on providers that use
        AWS SSM Parameter Store paths (``/aws/service/…``) as image
        specifications and need the TemplateConfigurationManager to resolve
        those paths to concrete AMI IDs before submitting to the cloud API.

        Only the AWS provider currently needs this; all other providers
        (k8s, GCP, Azure, …) use container image references that do not
        require pre-flight resolution.
        """
        return False

    # ------------------------------------------------------------------
    # Typed provisioning interface — returns OperationOutcome
    # ------------------------------------------------------------------

    @abstractmethod
    async def acquire(self, request: "Request") -> OperationOutcome:
        """Submit an acquisition request to the provider.

        The provider *accepts* the request and returns immediately.  Because
        most cloud providers (AWS EC2Fleet, SpotFleet, …) are async by nature
        the outcome is typically ``Accepted`` rather than ``Completed``.
        Callers must poll via :meth:`get_status` until a terminal outcome.

        Args:
            request: Domain request describing what resources to acquire.

        Returns:
            ``Accepted`` — provider accepted, instances pending.
            ``Completed`` — provider fulfilled synchronously (rare, e.g. dry-run).
            ``Failed``    — provider rejected or hard error.
        """

    @abstractmethod
    async def return_machines(self, machine_ids: list[str], request: "Request") -> OperationOutcome:
        """Submit a return (termination) request to the provider.

        AWS terminates asynchronously — the API call returns immediately while
        instances transition through ``shutting-down``.  The outcome is
        therefore ``Accepted`` with the terminating IDs as ``pending_resource_ids``.

        Args:
            machine_ids: Provider-side instance/resource IDs to terminate.
            request: Domain request providing context (provider_name, template, …).

        Returns:
            ``Accepted``  — termination accepted, instances shutting down.
            ``Completed`` — terminated synchronously (rare / mock).
            ``Failed``    — provider rejected or hard error.
        """

    @abstractmethod
    async def get_status(self, resource_ids: list[str], request: "Request") -> OperationOutcome:
        """Query the current status of previously submitted resources.

        Args:
            resource_ids: Provider-side resource IDs to check.
            request: Domain request providing context.

        Returns:
            ``Completed``      — all resources reached a terminal success state.
            ``Accepted``       — resources still in a non-terminal state.
            ``RequiresFollowUp`` — a specific follow-up action is needed.
            ``Failed``         — all resources failed or a hard error occurred.
        """
