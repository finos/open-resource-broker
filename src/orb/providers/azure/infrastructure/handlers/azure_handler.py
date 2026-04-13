"""Azure handler base class.

All Azure infrastructure handlers extend this ABC, providing a common constructor
contract and the three core operations
(acquire, status, release) that the provisioning adapter and strategy call.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, NotRequired, Optional, TypeAlias, TypedDict

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.request.aggregate import Request
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.infrastructure.azure_client import AzureClient
from orb.providers.azure.infrastructure.cyclecloud_session import CycleCloudRequestContext


class AzureAcquireHostsResult(TypedDict):
    """Normalized result returned by Azure create handlers."""

    success: bool
    resource_ids: list[str]
    instances: list[dict[str, Any]]
    error_message: NotRequired[str | None]
    provider_data: NotRequired[dict[str, Any]]


class AzureStatusProviderData(TypedDict, total=False):
    """Provider-owned metadata surfaced on Azure status results."""

    resource_id: str
    vm_name: str
    vm_id: str
    vmss_instance_id: str
    node_id: str
    node_name: str
    cluster_name: str
    node_array: str
    cc_state: str
    hostname: str
    resource_group: str
    location: str
    nic_id: str
    nic_name: str
    vnet_id: str
    fleet_errors: list[dict[str, Any]]


class AzureHandlerStatusResult(TypedDict, total=False):
    """Normalized status record returned by Azure handlers."""

    instance_id: str
    name: str
    resource_id: str
    status: str
    private_ip: str | None
    public_ip: str | None
    launch_time: str | None
    instance_type: str | None
    subnet_id: str | None
    vpc_id: str | None
    availability_zone: str | None
    provider_type: str
    error: str
    provider_data: AzureStatusProviderData


@dataclass(frozen=True)
class AzureReleaseContext:
    """Provider-owned runtime context required for Azure termination flows."""

    resource_group: str | None = None
    resource_id: str | None = None
    cyclecloud_request_context: CycleCloudRequestContext = field(
        default_factory=CycleCloudRequestContext
    )


class AzureSubmittedDeletion(TypedDict, total=False):
    """One submitted or attempted Azure deletion target."""

    requested_id: str
    vm_name: str
    error: str


class AzurePendingResourceCleanupMetadata(TypedDict, total=False):
    """Durable VMSS cleanup metadata persisted for follow-up reconciliation."""

    resource_group: str
    vmss_name: str
    machine_ids: list[str]
    delete_vmss_when_empty: bool
    member_delete_submitted: bool
    delete_submitted: bool
    delete_retry_pending: bool
    last_delete_error: str
    delete_submission_semantics: str


class AzureVmssReleaseProviderData(TypedDict, total=False):
    """Provider data returned when a VMSS termination request is submitted."""

    resource_group: str
    vmss_name: str
    operation_status: str
    submitted_deletions: list[AzureSubmittedDeletion]
    resolved_instance_ids: list[str]
    pending_resource_cleanup: AzurePendingResourceCleanupMetadata


class AzureSingleVmReleaseProviderData(TypedDict, total=False):
    """Provider data returned when SingleVM termination requests are submitted."""

    resource_group: str
    operation_status: str
    submitted_deletions: list[AzureSubmittedDeletion]


class AzureCycleCloudReleaseProviderData(TypedDict, total=False):
    """Provider data returned when CycleCloud termination requests are submitted."""

    cluster_name: str
    terminate_operation_location: str
    operation_status: str


AzureReleaseProviderData: TypeAlias = (
    AzureVmssReleaseProviderData
    | AzureSingleVmReleaseProviderData
    | AzureCycleCloudReleaseProviderData
)


class AzureReleaseHostsResult(TypedDict, total=False):
    """Normalized termination submission result returned by Azure handlers."""

    provider_data: AzureReleaseProviderData


@injectable
class AzureHandler(ABC):
    """Abstract base handler for Azure provisioning operations.

    Concrete implementations (``VMSSHandler``, ``SingleVMHandler``)
    implement the three abstract methods for their specific Azure API surface.
    """

    def __init__(
        self,
        azure_client: AzureClient,
        logger: LoggingPort,
    ) -> None:
        self.azure_client = azure_client
        self._logger = logger

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    @abstractmethod
    def acquire_hosts(
        self, request: Request, template: AzureTemplate
    ) -> AzureAcquireHostsResult:
        """Provision resources.

        Returns:
            ``AzureAcquireHostsResult`` with normalized create-operation fields.
        """

    @abstractmethod
    def check_hosts_status(self, request: Request) -> list[AzureHandlerStatusResult]:
        """Return list of instance detail dicts for ``request.resource_ids``.

        Each dict must include at minimum:
            instance_id, status, private_ip, public_ip,
            launch_time, instance_type, subnet_id, vpc_id
        """

    @abstractmethod
    def release_hosts(
        self,
        machine_ids: list[str],
        resource_id: str,
        context: Optional[AzureReleaseContext] = None,
    ) -> Optional[AzureReleaseHostsResult]:
        """Delete / deallocate cloud resources and optionally return provider metadata."""

    # ------------------------------------------------------------------
    # Optional helpers
    # ------------------------------------------------------------------

    @classmethod
    def get_example_templates(cls) -> list[dict[str, Any]]:
        """Return example template dicts for documentation / wizard use."""
        return []
