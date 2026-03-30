"""Azure client wrapper with lazy service client initialization.

This module provides a unified wrapper for Azure SDK interactions with:
- Lazy initialization of Azure service clients
- Explicit lifecycle cleanup for owned Azure SDK resources
- Configuration resolution via ProviderSelectionService / fallback
- Optional metrics instrumentation

Azure SDK clients wrapped:
- ComputeManagementClient (VMs, VMSS, Disks, Images, Galleries)
- NetworkManagementClient (VNets, Subnets, NICs, NSGs, Public IPs, LBs)
- ResourceManagementClient (Resource groups, deployments)
- ManagedServiceIdentityClient (User-assigned managed identities)
- AuthorizationManagementClient (Role assignments)
- MonitorManagementClient (Metrics, diagnostics)
- SubscriptionClient (Subscriptions, locations)

Note:
    Actual Azure SDK packages (``azure-identity``, ``azure-mgmt-compute``, etc.)
    are **not** imported at module level so that the rest of the codebase can be
    loaded and tested without them installed.  They are imported lazily inside
    property accessors the first time a client is requested.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Any, Optional, Protocol, cast

from orb.config import PerformanceConfig
from orb.domain.base.exceptions import ConfigurationError
from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.monitoring.metrics import MetricsCollector
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.exceptions.azure_exceptions import (
    AuthenticationError,
    AzureConfigurationError,
)
from orb.providers.azure.infrastructure.credential_factory import (
    AzureCredentialProtocol,
    create_default_azure_credential,
)


class TypedConfigPort(Protocol):
    """Minimal config interface for AzureClient.

    Only ``get_typed`` is used — the client resolves its
    ``AzureProviderConfig`` and ``PerformanceConfig`` through this
    single method.  Implementations include ``AzureInstanceConfigPort``
    (per-instance shim) and ``ConfigurationManager`` (global).
    """

    def get_typed(self, config_type: type) -> Any: ...


if TYPE_CHECKING:
    from azure.mgmt.authorization import AuthorizationManagementClient
    from azure.mgmt.compute import ComputeManagementClient
    from azure.mgmt.monitor import MonitorManagementClient
    from azure.mgmt.msi import ManagedServiceIdentityClient
    from azure.mgmt.network import NetworkManagementClient
    from azure.mgmt.resource import ResourceManagementClient
    from azure.mgmt.resource.subscriptions import SubscriptionClient

class AzureResourceRefProtocol(Protocol):
    """Minimal ARM resource reference carrying an Azure resource ID."""

    id: Optional[str]


class AzureNicReferencePropertiesProtocol(Protocol):
    """Subset of NIC reference properties used for primary-NIC ordering."""

    primary: Optional[bool]


class AzureNicReferenceProtocol(AzureResourceRefProtocol, Protocol):
    """NIC reference shape exposed from a VM network profile."""

    properties: Optional[AzureNicReferencePropertiesProtocol]


class AzureNetworkProfileProtocol(Protocol):
    """VM network profile surface needed for NIC reference enumeration."""

    network_interfaces: list[AzureNicReferenceProtocol]


class AzureIpConfigurationPropertiesProtocol(Protocol):
    """Fallback property bag exposed by Azure IP configuration objects."""

    private_ip_address: Optional[str]
    subnet: Optional[AzureResourceRefProtocol]
    public_ip_address: Optional[AzureResourceRefProtocol]


class AzureIpConfigurationProtocol(Protocol):
    """IP configuration fields used to resolve private/public network identity."""

    private_ip_address: Optional[str]
    subnet: Optional[AzureResourceRefProtocol]
    public_ip_address: Optional[AzureResourceRefProtocol]
    properties: Optional[AzureIpConfigurationPropertiesProtocol]


class AzureNicProtocol(Protocol):
    """NIC surface used to enumerate IP configurations."""

    ip_configurations: list[AzureIpConfigurationProtocol]


class AzurePublicIpProtocol(Protocol):
    """Public IP resource surface used to read the resolved IP address."""

    ip_address: Optional[str]


class AzureVmNetworkIdentityProtocol(Protocol):
    """VM surface used to enter the network-identity resolution flow."""

    network_profile: Optional[AzureNetworkProfileProtocol]


@dataclass(frozen=True)
class ParsedArmResourceId:
    """Validated ARM resource identifier components."""

    subscription_id: str
    resource_group: str
    provider_namespace: str
    resource_path_segments: tuple[str, ...]

    @property
    def resource_name(self) -> str:
        return self.resource_path_segments[-1]

    @property
    def resource_type(self) -> str:
        return self.resource_path_segments[-2]

    def parent_resource_id(self) -> Optional[str]:
        """Return the parent ARM resource ID for child resources."""
        if len(self.resource_path_segments) <= 2:
            return None

        return (
            f"/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{self.resource_group}"
            f"/providers/{self.provider_namespace}/"
            + "/".join(self.resource_path_segments[:-2])
        )


@injectable
class AzureClient:
    """Wrapper for Azure API interactions.

    * Configuration is resolved once during ``__init__`` via
      a config port shim (``AzureInstanceConfigPort``) injected at
      construction time.
    * Azure SDK management clients are created **lazily** on first access
      through ``@property`` accessors, keeping startup cost near zero.
    * An optional :class:`MetricsCollector` can be injected for API-call
      instrumentation (hooks are left as extension points for now).
    """

    def __init__(
        self,
        config: TypedConfigPort,
        logger: LoggingPort,
        metrics: Optional[MetricsCollector] = None,
    ) -> None:
        """Initialise the Azure client wrapper.

        Args:
            config: Configuration port for accessing provider settings.
            logger: Logger for diagnostic and operational messages.
            metrics: Optional metrics collector for Azure API instrumentation.
        """
        self._config_manager = config
        self._logger = logger
        self._azure_config: Optional[AzureProviderConfig] = None
        self._azure_config_loaded = False

        # Resolve provider configuration (region, subscription, auth, timeouts…)
        azure_provider_config = self._get_selected_azure_provider_config()

        self.region_name: str = (
            azure_provider_config.region if azure_provider_config and azure_provider_config.region else "eastus2"
        )
        self.subscription_id: Optional[str] = (
            azure_provider_config.subscription_id if azure_provider_config else None
        )
        self.resource_group: Optional[str] = (
            azure_provider_config.resource_group if azure_provider_config else None
        )

        self._logger.debug("Azure client region determined: %s", self.region_name)

        max_retries = self._resolve_int_config_value(
            field_name="max_retries",
            raw_value=azure_provider_config.max_retries if azure_provider_config else None,
            default=3,
            minimum=0,
        )
        connect_timeout = self._resolve_int_config_value(
            field_name="connect_timeout",
            raw_value=azure_provider_config.connect_timeout if azure_provider_config else None,
            default=30,
            minimum=1,
        )
        read_timeout = self._resolve_int_config_value(
            field_name="read_timeout",
            raw_value=azure_provider_config.read_timeout if azure_provider_config else None,
            default=60,
            minimum=1,
        )

        self._max_retries = max_retries
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout

        # Load performance configuration
        self.perf_config = self._load_performance_config()

        # Lazy Azure SDK client slots
        self._credential: Optional[AzureCredentialProtocol] = None
        self._compute_client: Optional[ComputeManagementClient] = None
        self._network_client: Optional[NetworkManagementClient] = None
        self._resource_client: Optional[ResourceManagementClient] = None
        self._msi_client: Optional[ManagedServiceIdentityClient] = None
        self._authorization_client: Optional[AuthorizationManagementClient] = None
        self._monitor_client: Optional[MonitorManagementClient] = None
        self._subscription_client: Optional[SubscriptionClient] = None
        self._credentials_validated = False
        self._closed = False
        self._lazy_init_lock = RLock()

        # Metrics instrumentation (extension point)
        self._metrics = metrics
        if metrics:
            logger.info("Azure API metrics collection enabled")
        else:
            logger.debug(
                "Azure API metrics collection disabled - no MetricsCollector provided"
            )

        self._logger.info(
            "Azure client initialised: region=%s, subscription=%s, resource_group=%s, "
            "retries=%d, connect_timeout=%ds, read_timeout=%ds",
            self.region_name,
            self.subscription_id or "(not set)",
            self.resource_group or "(not set)",
            max_retries,
            connect_timeout,
            read_timeout,
        )

    # ------------------------------------------------------------------
    # Configuration resolution
    # ------------------------------------------------------------------

    def _get_selected_azure_provider_config(self) -> Optional[AzureProviderConfig]:
        """Resolve the active Azure provider configuration.

        All construction paths (DI factory and ``create_azure_strategy``)
        wrap the already-resolved ``AzureProviderConfig`` in an
        ``_AzureInstanceConfigPort`` shim before passing it here.
        ``get_typed(AzureProviderConfig)`` returns the config directly —
        no discovery or selection is needed.

        The result is cached after the first call.
        """
        if self._azure_config_loaded:
            return self._azure_config

        self._azure_config_loaded = True

        try:
            self._azure_config = self._config_manager.get_typed(AzureProviderConfig)
        except ConfigurationError as exc:
            self._logger.debug("Could not load Azure provider config: %s", exc)
            return None

        if self._azure_config is not None:
            self._logger.debug("Loaded Azure provider config via configuration port")

        return self._azure_config

    def get_provider_config(self) -> Optional[AzureProviderConfig]:
        """Return the active Azure provider configuration, if available."""
        return self._get_selected_azure_provider_config()

    # ------------------------------------------------------------------
    # Credential management
    # ------------------------------------------------------------------

    def _management_client_kwargs(self) -> dict[str, Any]:
        """Common azure-core kwargs for management clients."""
        return {
            "retry_total": self._max_retries,
            "connection_timeout": self._connect_timeout,
            "read_timeout": self._read_timeout,
        }

    def _management_client_credential(self) -> Any:
        """Return the credential object to hand to Azure SDK client constructors.

        Internally we type against the small credential interface this module
        actually uses. The Azure SDK constructors are typed more nominally, so
        keep that boundary typed as ``Any`` rather than pretending our internal
        protocol is the SDK's class hierarchy.
        """
        return self.credential

    def _management_client_init_kwargs(
        self,
        *,
        requires_subscription_id: bool,
    ) -> dict[str, Any]:
        """Build constructor kwargs shared by Azure management clients."""
        client_kwargs = {
            "credential": self._management_client_credential(),
            **self._management_client_kwargs(),
        }
        if requires_subscription_id:
            self._ensure_subscription_id()
            client_kwargs["subscription_id"] = self.subscription_id
        return client_kwargs

    @staticmethod
    def _resolve_int_config_value(
            *,
        field_name: str,
        raw_value: Optional[int],
        default: int,
        minimum: int,
    ) -> int:
        """Resolve an integer config value with explicit validation."""
        if raw_value is None:
            return default

        try:
            value = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise AzureConfigurationError(
                f"Azure provider config '{field_name}' must be an integer, got {raw_value!r}"
            ) from exc

        if value < minimum:
            raise AzureConfigurationError(
                f"Azure provider config '{field_name}' must be >= {minimum}, got {value}"
            )

        return value

    def _ensure_open(self) -> None:
        """Raise if the Azure client has already been closed."""
        if self._closed:
            raise RuntimeError("AzureClient has been closed")

    def _build_compute_client(self) -> ComputeManagementClient:
        """Construct a Compute management client."""
        self._ensure_open()
        self._logger.debug("Initialising ComputeManagementClient on first use")
        try:
            from azure.mgmt.compute import ComputeManagementClient
        except ImportError as exc:
            raise AzureConfigurationError(
                "azure-mgmt-compute package is not installed"
            ) from exc
        return ComputeManagementClient(
            **self._management_client_init_kwargs(requires_subscription_id=True)
        )

    def _build_network_client(self) -> NetworkManagementClient:
        """Construct a Network management client."""
        self._ensure_open()
        self._logger.debug("Initialising NetworkManagementClient on first use")
        try:
            from azure.mgmt.network import NetworkManagementClient
        except ImportError as exc:
            raise AzureConfigurationError(
                "azure-mgmt-network package is not installed"
            ) from exc
        return NetworkManagementClient(
            **self._management_client_init_kwargs(requires_subscription_id=True)
        )

    def _build_resource_client(self) -> ResourceManagementClient:
        """Construct a Resource management client."""
        self._ensure_open()
        self._logger.debug("Initialising ResourceManagementClient on first use")
        try:
            from azure.mgmt.resource import ResourceManagementClient
        except ImportError as exc:
            raise AzureConfigurationError(
                "azure-mgmt-resource package is not installed"
            ) from exc
        return ResourceManagementClient(
            **self._management_client_init_kwargs(requires_subscription_id=True)
        )

    def _build_msi_client(self) -> ManagedServiceIdentityClient:
        """Construct a Managed Service Identity client."""
        self._ensure_open()
        self._logger.debug("Initialising ManagedServiceIdentityClient on first use")
        try:
            from azure.mgmt.msi import ManagedServiceIdentityClient
        except ImportError as exc:
            raise AzureConfigurationError(
                "azure-mgmt-msi package is not installed"
            ) from exc
        return ManagedServiceIdentityClient(
            **self._management_client_init_kwargs(requires_subscription_id=True)
        )

    def _build_authorization_client(self) -> AuthorizationManagementClient:
        """Construct an Authorization management client."""
        self._ensure_open()
        self._logger.debug("Initialising AuthorizationManagementClient on first use")
        try:
            from azure.mgmt.authorization import AuthorizationManagementClient
        except ImportError as exc:
            raise AzureConfigurationError(
                "azure-mgmt-authorization package is not installed"
            ) from exc
        return AuthorizationManagementClient(
            **self._management_client_init_kwargs(requires_subscription_id=True)
        )

    def _build_monitor_client(self) -> MonitorManagementClient:
        """Construct a Monitor management client."""
        self._ensure_open()
        self._logger.debug("Initialising MonitorManagementClient on first use")
        try:
            from azure.mgmt.monitor import MonitorManagementClient
        except ImportError as exc:
            raise AzureConfigurationError(
                "azure-mgmt-monitor package is not installed"
            ) from exc
        return MonitorManagementClient(
            **self._management_client_init_kwargs(requires_subscription_id=True)
        )

    def _build_subscription_client(self) -> SubscriptionClient:
        """Construct a Subscription client."""
        self._ensure_open()
        self._logger.debug("Initialising SubscriptionClient on first use")
        try:
            from azure.mgmt.resource.subscriptions import SubscriptionClient
        except ImportError as exc:
            raise AzureConfigurationError(
                "azure-mgmt-resource package is not installed"
            ) from exc
        return SubscriptionClient(
            **self._management_client_init_kwargs(requires_subscription_id=False)
        )

    @property
    def credential(self) -> AzureCredentialProtocol:
        """Return an Azure ``TokenCredential``, creating it on first access.

        Resolution order:

        1. ``DefaultAzureCredential`` — covers managed identity, VS Code,
           Azure CLI, environment variables, and workload identity federation
           out of the box.

        Raises:
            AuthenticationError: If no valid credential can be obtained.
        """
        with self._lazy_init_lock:
            self._ensure_open()
            if self._credential is None:
                self._logger.debug("Creating Azure credential on first use")
                try:
                    self._credential = create_default_azure_credential(
                        client_id=self._azure_config.client_id if self._azure_config else None,
                        logger=self._logger,
                    )
                except ImportError as exc:
                    raise AuthenticationError(
                        "azure-identity package is not installed"
                    ) from exc
            return self._credential

    def _close_management_client(
        self,
        resource_name: str,
        client: Any,
    ) -> None:
        """Close one concrete Azure SDK management client owned by this wrapper."""
        if client is None:
            return

        client.close()

        self._logger.debug("Closed Azure resource %s", resource_name)

    def _close_credential(
        self,
        credential: Optional[AzureCredentialProtocol],
    ) -> None:
        """Close the owned Azure credential."""
        if credential is None:
            return

        credential.close()

        self._logger.debug("Closed Azure resource credential")

    def close(self) -> None:
        """Close owned Azure SDK resources and prevent further use.

        The Azure client owns its lazily-created credentials and management
        clients, so cleanup belongs here rather than in unrelated orchestration
        layers.
        """
        close_errors: list[Exception] = []

        with self._lazy_init_lock:
            if self._closed:
                return

            def close_resource(close_fn: Any, *close_args: Any) -> None:
                resource_name = str(close_args[0])
                resource = close_args[-1]
                if resource is None:
                    return
                try:
                    close_fn(*close_args)
                except Exception as exc:  # pragma: no cover - exercised via public close tests
                    close_errors.append(exc)
                    self._logger.warning(
                        "Failed closing Azure resource %s: %s",
                        resource_name,
                        exc,
                    )

            subscription_client = self._subscription_client
            self._subscription_client = None
            close_resource(self._close_management_client, "subscription_client", subscription_client)

            monitor_client = self._monitor_client
            self._monitor_client = None
            close_resource(self._close_management_client, "monitor_client", monitor_client)

            authorization_client = self._authorization_client
            self._authorization_client = None
            close_resource(self._close_management_client, "authorization_client", authorization_client)

            msi_client = self._msi_client
            self._msi_client = None
            close_resource(self._close_management_client, "msi_client", msi_client)

            resource_client = self._resource_client
            self._resource_client = None
            close_resource(self._close_management_client, "resource_client", resource_client)

            network_client = self._network_client
            self._network_client = None
            close_resource(self._close_management_client, "network_client", network_client)

            compute_client = self._compute_client
            self._compute_client = None
            close_resource(self._close_management_client, "compute_client", compute_client)

            credential = self._credential
            self._credential = None
            close_resource(self._close_credential, credential)

            self._credentials_validated = False
            self._closed = True

        if close_errors:
            raise close_errors[0]

    def __enter__(self) -> AzureClient:
        """Enter a managed Azure client scope."""
        self._ensure_open()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        """Exit a managed Azure client scope and close owned resources."""
        self.close()

    # ------------------------------------------------------------------
    # Lazy management-client properties
    # ------------------------------------------------------------------

    @property
    def compute_client(self) -> ComputeManagementClient:
        """Lazy initialisation of Azure Compute management client.

        Provides access to VMs, VMSS, Disks, Images, Availability Sets,
        Proximity Placement Groups, and Galleries.
        """
        with self._lazy_init_lock:
            self._ensure_open()
            if self._compute_client is None:
                self._compute_client = self._build_compute_client()
            return self._compute_client

    @property
    def network_client(self) -> NetworkManagementClient:
        """Lazy initialisation of Azure Network management client.

        Provides access to VNets, Subnets, NICs, NSGs, Public IPs, and
        Load Balancers.
        """
        with self._lazy_init_lock:
            self._ensure_open()
            if self._network_client is None:
                self._network_client = self._build_network_client()
            return self._network_client

    @property
    def resource_client(self) -> ResourceManagementClient:
        """Lazy initialisation of Azure Resource management client.

        Provides access to resource groups, deployments, and providers.
        """
        with self._lazy_init_lock:
            self._ensure_open()
            if self._resource_client is None:
                self._resource_client = self._build_resource_client()
            return self._resource_client

    @property
    def msi_client(self) -> ManagedServiceIdentityClient:
        """Lazy initialisation of Azure Managed Service Identity client.

        Provides access to user-assigned managed identities.
        """
        with self._lazy_init_lock:
            self._ensure_open()
            if self._msi_client is None:
                self._msi_client = self._build_msi_client()
            return self._msi_client

    @property
    def authorization_client(self) -> AuthorizationManagementClient:
        """Lazy initialisation of Azure Authorization management client.

        Provides access to role definitions and role assignments.
        """
        with self._lazy_init_lock:
            self._ensure_open()
            if self._authorization_client is None:
                self._authorization_client = self._build_authorization_client()
            return self._authorization_client

    @property
    def monitor_client(self) -> MonitorManagementClient:
        """Lazy initialisation of Azure Monitor management client.

        Provides access to metrics, diagnostic settings, and activity logs.
        """
        with self._lazy_init_lock:
            self._ensure_open()
            if self._monitor_client is None:
                self._monitor_client = self._build_monitor_client()
            return self._monitor_client

    @property
    def subscription_client(self) -> SubscriptionClient:
        """Lazy initialisation of Azure Subscription client.

        Provides access to subscription and location information.
        Does **not** require ``subscription_id`` at construction time.
        """
        with self._lazy_init_lock:
            self._ensure_open()
            if self._subscription_client is None:
                self._subscription_client = self._build_subscription_client()
            return self._subscription_client

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def validate_credentials(self) -> bool:
        """Validate that the current credential can authenticate.

        Returns:
            ``True`` if credentials are valid, ``False`` otherwise.
        """
        if self._credentials_validated:
            return True

        try:
            # Lightweight call to verify token acquisition
            self.credential.get_token("https://management.azure.com/.default")
            self._credentials_validated = True
            self._logger.info("Azure credentials validated successfully")
            return True
        except self._credential_validation_error_types() as exc:
            self._logger.error("Azure credential validation failed: %s", exc)
            return False

    def validate_subscription(self) -> bool:
        """Validate that the configured subscription is accessible.

        Returns:
            ``True`` if the subscription is accessible, ``False`` otherwise.
        """
        if not self.subscription_id:
            self._logger.error("No subscription_id configured")
            return False

        try:
            sub = self.subscription_client.subscriptions.get(self.subscription_id)
            self._logger.info(
                "Azure subscription validated: %s (%s)",
                sub.display_name,
                sub.state,
            )
            return True
        except self._subscription_validation_error_types() as exc:
            self._logger.error(
                "Azure subscription validation failed for %s: %s",
                self.subscription_id,
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Performance / caching configuration
    # ------------------------------------------------------------------

    @staticmethod
    def _default_performance_config() -> dict[str, Any]:
        """Return the default Azure client performance settings."""
        return {
            "enable_batching": True,
            "batch_sizes": {
                "deallocate_vms": 25,
                "create_tags": 20,
                "describe_vms": 25,
            },
            "enable_parallel": True,
            "max_workers": 10,
            "enable_caching": True,
            "cache_ttl": 300,
        }

    @classmethod
    def _map_performance_config(cls, perf_config: PerformanceConfig) -> dict[str, Any]:
        """Project the shared performance config onto Azure client settings."""
        performance_settings = cls._default_performance_config()
        performance_settings.update(
            {
                "enable_batching": perf_config.enable_batching,
                "enable_parallel": perf_config.enable_parallel,
                "max_workers": perf_config.max_workers,
                "enable_caching": perf_config.caching.request_status.enabled,
                "cache_ttl": perf_config.caching.request_status.ttl_seconds,
            }
        )
        return performance_settings

    def _load_performance_config(self) -> dict[str, Any]:
        """Load performance configuration from the configuration port."""
        try:
            perf_config = self._config_manager.get_typed(PerformanceConfig)
        except ConfigurationError as exc:
            self._logger.debug(
                "Could not load performance config from ConfigurationManager: %s",
                exc,
            )
            return self._default_performance_config()

        if not isinstance(perf_config, PerformanceConfig):
            self._logger.debug(
                "Ignoring unexpected performance config type: %s",
                type(perf_config).__name__,
            )
            return self._default_performance_config()

        self._logger.debug("Loaded performance configuration from ConfigurationManager")
        return self._map_performance_config(perf_config)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_subscription_id(self) -> None:
        """Raise if ``subscription_id`` is not configured."""
        if not self.subscription_id:
            raise AzureConfigurationError(
                "subscription_id is required but not configured. "
                "Set it in the Azure provider configuration."
            )

    @staticmethod
    def _credential_validation_error_types() -> tuple[type[BaseException], ...]:
        """Return errors that should result in a soft credential validation failure."""
        azure_error_type: Optional[type[BaseException]]
        client_authentication_error_type: Optional[type[BaseException]]
        error_types: list[type[BaseException]] = [AuthenticationError]
        try:
            from azure.core.exceptions import AzureError, ClientAuthenticationError

            azure_error_type = AzureError
            client_authentication_error_type = ClientAuthenticationError
        except ImportError:
            azure_error_type = None
            client_authentication_error_type = None

        if azure_error_type is not None:
            error_types.append(azure_error_type)
        if client_authentication_error_type is not None:
            error_types.append(client_authentication_error_type)

        return tuple(dict.fromkeys(error_types))

    @classmethod
    def _subscription_validation_error_types(cls) -> tuple[type[BaseException], ...]:
        """Return errors that should result in a soft subscription validation failure."""
        error_types: list[type[BaseException]] = [AzureConfigurationError]
        error_types.extend(cls._credential_validation_error_types())
        return tuple(dict.fromkeys(error_types))

    @staticmethod
    def _network_lookup_error_types() -> tuple[type[BaseException], ...]:
        """Return Azure/network lookup errors that should not abort status enrichment."""
        azure_error_type: Optional[type[BaseException]]
        error_types: list[type[BaseException]] = [AzureConfigurationError]
        try:
            from azure.core.exceptions import AzureError

            azure_error_type = AzureError
        except ImportError:
            azure_error_type = None

        if azure_error_type is not None:
            error_types.append(azure_error_type)

        return tuple(dict.fromkeys(error_types))

    @staticmethod
    def _network_profile_from_vm(vm: Any) -> Optional[AzureNetworkProfileProtocol]:
        return cast(AzureVmNetworkIdentityProtocol, vm).network_profile

    @staticmethod
    def _network_interface_refs_from_profile(
        network_profile: Optional[AzureNetworkProfileProtocol],
    ) -> list[AzureNicReferenceProtocol]:
        if network_profile is None:
            return []
        return list(network_profile.network_interfaces or [])

    @staticmethod
    def _is_primary_nic_ref(nic_ref: AzureNicReferenceProtocol) -> bool:
        nic_properties = nic_ref.properties
        if nic_properties is None:
            return False
        return bool(nic_properties.primary)

    @staticmethod
    def _resource_id(value: Optional[AzureResourceRefProtocol]) -> Optional[str]:
        if value is None:
            return None
        return value.id

    @staticmethod
    def _ip_configurations_from_nic(nic: Any) -> list[AzureIpConfigurationProtocol]:
        return list(cast(AzureNicProtocol, nic).ip_configurations or [])

    @staticmethod
    def _private_ip_from_ip_config(ip_config: AzureIpConfigurationProtocol) -> Optional[str]:
        if ip_config.private_ip_address:
            return ip_config.private_ip_address
        ip_properties = ip_config.properties
        if ip_properties is None:
            return None
        return ip_properties.private_ip_address

    @staticmethod
    def _subnet_from_ip_config(
        ip_config: AzureIpConfigurationProtocol,
    ) -> Optional[AzureResourceRefProtocol]:
        if ip_config.subnet is not None:
            return ip_config.subnet
        ip_properties = ip_config.properties
        if ip_properties is None:
            return None
        return ip_properties.subnet

    @staticmethod
    def _public_ip_ref_from_ip_config(
        ip_config: AzureIpConfigurationProtocol,
    ) -> Optional[AzureResourceRefProtocol]:
        if ip_config.public_ip_address is not None:
            return ip_config.public_ip_address
        ip_properties = ip_config.properties
        if ip_properties is None:
            return None
        return ip_properties.public_ip_address

    @staticmethod
    def _public_ip_address_from_resource(public_ip_resource: Any) -> Optional[str]:
        return cast(AzurePublicIpProtocol, public_ip_resource).ip_address

    @classmethod
    def _parse_arm_resource_id(
        cls,
        arm_id: str,
    ) -> Optional[ParsedArmResourceId]:
        """Parse an ARM resource ID only when it matches the canonical shape."""
        raw_arm_id = str(arm_id).strip()
        if not raw_arm_id:
            return None

        stripped = raw_arm_id.strip("/")
        if not stripped:
            return None

        parts = stripped.split("/")
        if any(not segment for segment in parts):
            return None

        if len(parts) < 8:
            return None

        if parts[0].lower() != "subscriptions" or parts[2].lower() != "resourcegroups":
            return None

        if parts[4].lower() != "providers":
            return None

        subscription_id = parts[1]
        resource_group = parts[3]
        provider_namespace = parts[5]
        resource_path_segments = tuple(parts[6:])

        if not subscription_id or not resource_group or not provider_namespace:
            return None

        if len(resource_path_segments) < 2 or len(resource_path_segments) % 2 != 0:
            return None

        return ParsedArmResourceId(
            subscription_id=subscription_id,
            resource_group=resource_group,
            provider_namespace=provider_namespace,
            resource_path_segments=resource_path_segments,
        )

    @classmethod
    def extract_resource_group_and_name_from_arm_id(
        cls,
        arm_id: str,
    ) -> Optional[tuple[str, str]]:
        """Extract ``(resource_group, resource_name)`` from an ARM resource ID."""
        parsed_arm_id = cls._parse_arm_resource_id(arm_id)
        if parsed_arm_id is None:
            return None
        return parsed_arm_id.resource_group, parsed_arm_id.resource_name

    @classmethod
    def subnet_id_to_vnet_id(cls, subnet_id: Optional[str]) -> Optional[str]:
        """Return the parent VNet ARM ID from a subnet ARM ID."""
        if not subnet_id:
            return None
        parsed_arm_id = cls._parse_arm_resource_id(subnet_id)
        if parsed_arm_id is None:
            return None
        if parsed_arm_id.resource_type.lower() != "subnets":
            return None
        return parsed_arm_id.parent_resource_id()

    def resolve_network_identity_from_vm(self, vm: Any) -> dict[str, Any]:
        """Resolve network identity fields from a VM or VMSS VM object."""
        net_profile = self._network_profile_from_vm(vm)
        nic_refs = self._network_interface_refs_from_profile(net_profile)
        return self.resolve_network_identity_from_nic_refs(nic_refs)

    def resolve_network_identity_from_nic_refs(
        self,
        nic_refs: list[AzureNicReferenceProtocol],
    ) -> dict[str, Any]:
        """Resolve private/public IP and subnet/VNet identity from NIC refs."""
        network_identity = {
            "private_ip": None,
            "public_ip": None,
            "subnet_id": None,
            "vnet_id": None,
            "nic_id": None,
            "nic_name": None,
        }
        if not nic_refs:
            return network_identity

        ordered_refs = sorted(
            nic_refs,
            key=lambda ref: not self._is_primary_nic_ref(ref),
        )

        for nic_ref in ordered_refs:
            nic_id = self._resource_id(nic_ref)
            if not nic_id:
                continue

            nic_lookup = self.extract_resource_group_and_name_from_arm_id(str(nic_id))
            if not nic_lookup:
                continue

            nic_rg, nic_name = nic_lookup
            try:
                nic = self.network_client.network_interfaces.get(
                    resource_group_name=nic_rg,
                    network_interface_name=nic_name,
                )
            except self._network_lookup_error_types() as exc:
                self._logger.debug("Failed to resolve NIC %s: %s", nic_id, exc)
                continue

            ip_configs = self._ip_configurations_from_nic(nic)
            for ip_cfg in ip_configs:
                private_ip = self._private_ip_from_ip_config(ip_cfg)
                subnet = self._subnet_from_ip_config(ip_cfg)
                subnet_id = self._resource_id(subnet)
                public_ip_ref = self._public_ip_ref_from_ip_config(ip_cfg)

                public_ip = None
                public_ip_id = self._resource_id(public_ip_ref)
                if public_ip_id:
                    public_ip_lookup = self.extract_resource_group_and_name_from_arm_id(
                        str(public_ip_id)
                    )
                    if public_ip_lookup:
                        pip_rg, pip_name = public_ip_lookup
                        try:
                            pip = self.network_client.public_ip_addresses.get(
                                resource_group_name=pip_rg,
                                public_ip_address_name=pip_name,
                            )
                            public_ip = self._public_ip_address_from_resource(pip)
                        except self._network_lookup_error_types() as exc:
                            self._logger.debug(
                                "Failed to resolve public IP %s: %s", public_ip_id, exc
                            )

                network_identity.update(
                    {
                        "private_ip": private_ip,
                        "public_ip": public_ip,
                        "subnet_id": subnet_id,
                        "vnet_id": self.subnet_id_to_vnet_id(subnet_id),
                        "nic_id": nic_id,
                        "nic_name": nic_name,
                    }
                )
                return network_identity

        return network_identity

    # ------------------------------------------------------------------
    # Metrics / observability
    # ------------------------------------------------------------------

    def get_metrics_stats(self) -> dict[str, Any]:
        """Return metrics collection statistics.

        Returns:
            Dictionary with metrics status; currently a placeholder for
            future instrumentation parity with the AWS client.
        """
        return {"metrics_enabled": self._metrics is not None}
