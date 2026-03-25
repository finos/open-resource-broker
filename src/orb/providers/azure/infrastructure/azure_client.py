"""Azure client wrapper with lazy service client initialization.

This module provides a unified wrapper for Azure SDK interactions with:
- Lazy initialization of Azure service clients
- Configuration resolution via ProviderSelectionService / fallback
- Optional metrics instrumentation
- Thread-safe resource caching and adaptive batch sizing

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

import threading
from typing import TYPE_CHECKING, Any, Optional, Protocol

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import ConfigurationPort, LoggingPort
from orb.monitoring.metrics import MetricsCollector
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.exceptions.azure_exceptions import (
    AuthenticationError,
    AzureConfigurationError,
)

if TYPE_CHECKING:
    from azure.mgmt.authorization import AuthorizationManagementClient
    from azure.mgmt.compute import ComputeManagementClient
    from azure.mgmt.monitor import MonitorManagementClient
    from azure.mgmt.msi import ManagedServiceIdentityClient
    from azure.mgmt.network import NetworkManagementClient
    from azure.mgmt.resource import ResourceManagementClient
    from azure.mgmt.resource.subscriptions import SubscriptionClient


class AzureCredentialProtocol(Protocol):
    """Credential surface this module requires from Azure identity objects."""

    def get_token(self, *scopes: str, **kwargs: Any) -> Any: ...


@injectable
class AzureClient:
    """Wrapper for Azure API interactions.

    * Configuration is resolved once during ``__init__`` via
      ``ProviderSelectionService`` (primary) or the legacy
      ``ConfigurationPort.get_typed`` path (fallback).
    * Azure SDK management clients are created **lazily** on first access
      through ``@property`` accessors, keeping startup cost near zero.
    * An optional :class:`MetricsCollector` can be injected for API-call
      instrumentation (hooks are left as extension points for now).
    * Thread-safe caching and batch-sizing state is initialised
    """

    def __init__(
        self,
        config: ConfigurationPort,
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
            getattr(azure_provider_config, "region", None) or "eastus2"
        )
        self.subscription_id: Optional[str] = (
            azure_provider_config.subscription_id if azure_provider_config else None
        )
        self.resource_group: Optional[str] = (
            azure_provider_config.resource_group if azure_provider_config else None
        )

        self._logger.debug("Azure client region determined: %s", self.region_name)

        max_retries = int(azure_provider_config.max_retries) if azure_provider_config else 3
        connect_timeout = (
            int(azure_provider_config.connect_timeout) if azure_provider_config else 30
        )
        read_timeout = (
            int(azure_provider_config.read_timeout) if azure_provider_config else 60
        )

        self._max_retries = max_retries
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout

        # Load performance configuration
        self.perf_config = self._load_performance_config()

        # Thread-safe resource cache
        self._resource_cache: dict[str, Any] = {}
        self._cache_lock = threading.RLock()

        # Adaptive batch sizing
        self._batch_history: dict[str, Any] = {}
        self._batch_sizes = self.perf_config.get("batch_sizes", {}).copy()
        self._batch_lock = threading.RLock()

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

        Primary path: ``ProviderSelectionService`` picks the active provider
        instance and its ``AzureProviderConfig`` payload.

        Fallback: ``ConfigurationPort.get_typed(AzureProviderConfig)``.

        The result is cached after the first successful (or failed) attempt
        so that repeated calls are free.
        """
        if self._azure_config_loaded:
            return self._azure_config

        self._azure_config_loaded = True

        typed_config_fallback: Optional[AzureProviderConfig] = None
        try:
            typed_config_fallback = self._config_manager.get_typed(AzureProviderConfig)
            if typed_config_fallback is not None:
                self._logger.debug("Loaded Azure config from typed configuration fallback")
        except Exception as e:
            self._logger.debug(
                "Could not get Azure config from legacy config: %s", str(e)
            )

        provider_config = None
        try:
            provider_config = self._config_manager.get_provider_config()
        except Exception as e:
            self._logger.debug(
                "Could not get provider config from config manager: %s", str(e)
            )

        # For per-instance DI shims, the typed Azure config is already authoritative.
        if provider_config is None and typed_config_fallback is not None:
            self._azure_config = typed_config_fallback
            return typed_config_fallback

        # --- Primary: provider selection service --------------------------
        try:
            from orb.application.services.provider_registry_service import (
                ProviderRegistryService,
            )
            from orb.infrastructure.di.container import get_container

            container = get_container()
            selection_service = container.get(ProviderRegistryService)
            selection_result = selection_service.select_active_provider()

            self._logger.debug(
                "Provider selection result: type=%s, instance=%s",
                selection_result.provider_type,
                selection_result.provider_instance,
            )

            if selection_result.provider_type not in ("azure", "Azure"):
                if typed_config_fallback is not None:
                    self._logger.debug(
                        "Selected provider is not Azure (%s); using typed Azure config fallback",
                        selection_result.provider_type,
                    )
                    self._azure_config = typed_config_fallback
                    return typed_config_fallback
                raise AzureConfigurationError(
                    f"Selected provider is not Azure: {selection_result.provider_type}"
                )

            if provider_config:
                for provider in provider_config.providers:
                    if provider.name == selection_result.provider_instance:
                        self._logger.debug(
                            "Found provider %s, building AzureProviderConfig",
                            provider.name,
                        )
                        if not hasattr(provider, "config") or not provider.config:
                            self._logger.debug("Provider has no config attribute")
                            break

                        if isinstance(provider.config, AzureProviderConfig):
                            self._azure_config = provider.config
                            return self._azure_config

                        if isinstance(provider.config, dict):
                            self._azure_config = AzureProviderConfig(**provider.config)
                            return self._azure_config

                        config_data = None
                        if hasattr(provider.config, "model_dump"):
                            config_data = provider.config.model_dump()
                        elif hasattr(provider.config, "dict"):
                            config_data = provider.config.dict()
                        elif hasattr(provider.config, "__dict__"):
                            config_data = provider.config.__dict__

                        if isinstance(config_data, dict):
                            self._azure_config = AzureProviderConfig(**config_data)
                            return self._azure_config

                        self._logger.debug(
                            "Unsupported provider config type: %s",
                            type(provider.config),
                        )
                        break
                else:
                    self._logger.debug(
                        "Provider %s not found in config",
                        selection_result.provider_instance,
                    )
        except AzureConfigurationError:
            raise
        except Exception as e:
            self._logger.debug(
                "Could not get Azure config via provider selection: %s", str(e)
            )

        # --- Fallback: legacy typed config --------------------------------
        if typed_config_fallback is not None:
            self._azure_config = typed_config_fallback
            return typed_config_fallback

        return None

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
        if self._credential is None:
            self._logger.debug("Creating Azure credential on first use")
            try:
                from azure.identity import DefaultAzureCredential
                credential_kwargs: dict[str, Any] = {}
                if self._azure_config and self._azure_config.client_id:
                    credential_kwargs["managed_identity_client_id"] = self._azure_config.client_id

                self._credential = DefaultAzureCredential(**credential_kwargs)
                self._logger.info("Azure DefaultAzureCredential initialised")
            except ImportError as exc:
                raise AuthenticationError(
                    "azure-identity package is not installed"
                ) from exc
            except Exception as exc:
                raise AuthenticationError(
                    f"Failed to create Azure credential: {exc}"
                ) from exc
        return self._credential

    # ------------------------------------------------------------------
    # Lazy management-client properties
    # ------------------------------------------------------------------

    @property
    def compute_client(self) -> "ComputeManagementClient":
        """Lazy initialisation of Azure Compute management client.

        Provides access to VMs, VMSS, Disks, Images, Availability Sets,
        Proximity Placement Groups, and Galleries.
        """
        if self._compute_client is None:
            self._logger.debug("Initialising ComputeManagementClient on first use")
            self._ensure_subscription_id()
            try:
                from azure.mgmt.compute import ComputeManagementClient

                self._compute_client = ComputeManagementClient(
                    credential=self._management_client_credential(),
                    subscription_id=self.subscription_id,
                    **self._management_client_kwargs(),
                )
            except ImportError as exc:
                raise AzureConfigurationError(
                    "azure-mgmt-compute package is not installed"
                ) from exc
        return self._compute_client

    @property
    def network_client(self) -> "NetworkManagementClient":
        """Lazy initialisation of Azure Network management client.

        Provides access to VNets, Subnets, NICs, NSGs, Public IPs, and
        Load Balancers.
        """
        if self._network_client is None:
            self._logger.debug("Initialising NetworkManagementClient on first use")
            self._ensure_subscription_id()
            try:
                from azure.mgmt.network import NetworkManagementClient

                self._network_client = NetworkManagementClient(
                    credential=self._management_client_credential(),
                    subscription_id=self.subscription_id,
                    **self._management_client_kwargs(),
                )
            except ImportError as exc:
                raise AzureConfigurationError(
                    "azure-mgmt-network package is not installed"
                ) from exc
        return self._network_client

    @property
    def resource_client(self) -> "ResourceManagementClient":
        """Lazy initialisation of Azure Resource management client.

        Provides access to resource groups, deployments, and providers.
        """
        if self._resource_client is None:
            self._logger.debug("Initialising ResourceManagementClient on first use")
            self._ensure_subscription_id()
            try:
                from azure.mgmt.resource import ResourceManagementClient

                self._resource_client = ResourceManagementClient(
                    credential=self._management_client_credential(),
                    subscription_id=self.subscription_id,
                    **self._management_client_kwargs(),
                )
            except ImportError as exc:
                raise AzureConfigurationError(
                    "azure-mgmt-resource package is not installed"
                ) from exc
        return self._resource_client

    @property
    def msi_client(self) -> "ManagedServiceIdentityClient":
        """Lazy initialisation of Azure Managed Service Identity client.

        Provides access to user-assigned managed identities.
        """
        if self._msi_client is None:
            self._logger.debug("Initialising ManagedServiceIdentityClient on first use")
            self._ensure_subscription_id()
            try:
                from azure.mgmt.msi import ManagedServiceIdentityClient

                self._msi_client = ManagedServiceIdentityClient(
                    credential=self._management_client_credential(),
                    subscription_id=self.subscription_id,
                    **self._management_client_kwargs(),
                )
            except ImportError as exc:
                raise AzureConfigurationError(
                    "azure-mgmt-msi package is not installed"
                ) from exc
        return self._msi_client

    @property
    def authorization_client(self) -> "AuthorizationManagementClient":
        """Lazy initialisation of Azure Authorization management client.

        Provides access to role definitions and role assignments.
        """
        if self._authorization_client is None:
            self._logger.debug(
                "Initialising AuthorizationManagementClient on first use"
            )
            self._ensure_subscription_id()
            try:
                from azure.mgmt.authorization import AuthorizationManagementClient

                self._authorization_client = AuthorizationManagementClient(
                    credential=self._management_client_credential(),
                    subscription_id=self.subscription_id,
                    **self._management_client_kwargs(),
                )
            except ImportError as exc:
                raise AzureConfigurationError(
                    "azure-mgmt-authorization package is not installed"
                ) from exc
        return self._authorization_client

    @property
    def monitor_client(self) -> MonitorManagementClient:
        """Lazy initialisation of Azure Monitor management client.

        Provides access to metrics, diagnostic settings, and activity logs.
        """
        if self._monitor_client is None:
            self._logger.debug("Initialising MonitorManagementClient on first use")
            self._ensure_subscription_id()
            try:
                from azure.mgmt.monitor import MonitorManagementClient

                self._monitor_client = MonitorManagementClient(
                    credential=self._management_client_credential(),
                    subscription_id=self.subscription_id,
                    **self._management_client_kwargs(),
                )
            except ImportError as exc:
                raise AzureConfigurationError(
                    "azure-mgmt-monitor package is not installed"
                ) from exc
        return self._monitor_client

    @property
    def subscription_client(self) -> SubscriptionClient:
        """Lazy initialisation of Azure Subscription client.

        Provides access to subscription and location information.
        Does **not** require ``subscription_id`` at construction time.
        """
        if self._subscription_client is None:
            self._logger.debug("Initialising SubscriptionClient on first use")
            try:
                from azure.mgmt.resource.subscriptions import SubscriptionClient

                self._subscription_client = SubscriptionClient(
                    credential=self._management_client_credential(),
                    **self._management_client_kwargs(),
                )
            except ImportError as exc:
                raise AzureConfigurationError(
                    "azure-mgmt-resource package is not installed"
                ) from exc
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
        except Exception as exc:
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
        except Exception as exc:
            self._logger.error(
                "Azure subscription validation failed for %s: %s",
                self.subscription_id,
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Performance / caching configuration
    # ------------------------------------------------------------------

    def _load_performance_config(self) -> dict[str, Any]:
        """Load performance configuration from the configuration port.

        Returns:
            Performance configuration dictionary with sensible defaults.
        """
        try:
            from orb.config import PerformanceConfig

            perf_config = self._config_manager.get_typed(PerformanceConfig)
            if perf_config:
                self._logger.debug(
                    "Loaded performance configuration from ConfigurationManager"
                )
                return {
                    "enable_batching": perf_config.enable_batching,
                    "batch_sizes": {
                        "deallocate_vms": perf_config.batch_sizes.terminate_instances
                        if hasattr(perf_config.batch_sizes, "terminate_instances")
                        else 25,
                        "create_tags": perf_config.batch_sizes.create_tags
                        if hasattr(perf_config.batch_sizes, "create_tags")
                        else 20,
                        "describe_vms": perf_config.batch_sizes.describe_instances
                        if hasattr(perf_config.batch_sizes, "describe_instances")
                        else 25,
                    },
                    "enable_parallel": perf_config.enable_parallel,
                    "max_workers": perf_config.max_workers,
                    "enable_caching": perf_config.enable_caching,
                    "cache_ttl": perf_config.cache_ttl,
                }
        except Exception as e:
            self._logger.debug(
                "Could not load performance config from ConfigurationManager: %s",
                e,
            )

        # Sensible defaults
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
    def _sdk_attr(value: Any, attr: str, default: Any = None) -> Any:
        """Read an SDK-style attribute from an Azure SDK model or test double."""
        if value is None:
            return default
        if hasattr(value, attr):
            return getattr(value, attr)
        return default

    @classmethod
    def extract_resource_group_and_name_from_arm_id(
        cls,
        arm_id: str,
    ) -> Optional[tuple[str, str]]:
        """Extract ``(resource_group, resource_name)`` from an ARM resource ID."""
        parts = [segment for segment in str(arm_id).split("/") if segment]
        if not parts:
            return None

        try:
            rg_index = next(
                idx for idx, value in enumerate(parts) if value.lower() == "resourcegroups"
            )
            resource_group = parts[rg_index + 1]
            resource_name = parts[-1]
            if resource_group and resource_name:
                return resource_group, resource_name
        except Exception:
            return None
        return None

    @staticmethod
    def subnet_id_to_vnet_id(subnet_id: Optional[str]) -> Optional[str]:
        """Return the parent VNet ARM ID from a subnet ARM ID."""
        if not subnet_id:
            return None
        marker = "/subnets/"
        if marker not in subnet_id:
            return None
        return subnet_id.split(marker, 1)[0]

    def resolve_network_identity_from_vm(self, vm: Any) -> dict[str, Any]:
        """Resolve network identity fields from a VM or VMSS VM object."""
        net_profile = self._sdk_attr(vm, "network_profile")
        nic_refs = self._sdk_attr(net_profile, "network_interfaces", []) or []
        return self.resolve_network_identity_from_nic_refs(nic_refs)

    def resolve_network_identity_from_nic_refs(self, nic_refs: list[Any]) -> dict[str, Any]:
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
            key=lambda ref: (
                not bool(self._sdk_attr(self._sdk_attr(ref, "properties", {}), "primary", False))
            ),
        )

        for nic_ref in ordered_refs:
            nic_id = self._sdk_attr(nic_ref, "id")
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
            except Exception as exc:
                self._logger.debug("Failed to resolve NIC %s: %s", nic_id, exc)
                continue

            ip_configs = self._sdk_attr(nic, "ip_configurations", []) or []
            for ip_cfg in ip_configs:
                ip_cfg_props = self._sdk_attr(ip_cfg, "properties", {})
                private_ip = self._sdk_attr(ip_cfg, "private_ip_address") or self._sdk_attr(
                    ip_cfg_props, "private_ip_address"
                )
                subnet = self._sdk_attr(ip_cfg, "subnet") or self._sdk_attr(ip_cfg_props, "subnet")
                subnet_id = self._sdk_attr(subnet, "id")
                public_ip_ref = self._sdk_attr(ip_cfg, "public_ip_address") or self._sdk_attr(
                    ip_cfg_props, "public_ip_address"
                )

                public_ip = None
                public_ip_id = self._sdk_attr(public_ip_ref, "id")
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
                            public_ip = self._sdk_attr(pip, "ip_address")
                        except Exception as exc:
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
