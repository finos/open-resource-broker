"""Azure client wrapper with lazy service client initialization.

This module provides an integrated wrapper for Azure SDK interactions with:
- Lazy initialization of Azure service clients
- Explicit lifecycle cleanup for owned Azure SDK resources
- Explicit runtime config assembly before client construction

Azure SDK clients wrapped:
- ComputeManagementClient (VMs, VMSS, Disks, Images, Galleries)
- NetworkManagementClient (VNets, Subnets, NICs, NSGs, Public IPs, LBs)
- ResourceManagementClient (Resource groups, deployments)
- SubscriptionClient (Subscriptions, locations)

Note:
    Actual Azure SDK packages (``azure-identity``, ``azure-mgmt-compute``, etc.)
    are **not** imported at module level so that the rest of the codebase can be
    loaded and tested without them installed.  They are imported lazily inside
    property accessors the first time a client is requested.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING, Any, Callable, Optional

from orb.config import PerformanceConfig
from orb.domain.base.ports import LoggingPort
from orb.infrastructure.di.injectable import injectable
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.exceptions.azure_exceptions import (
    AuthenticationError,
    AzureConfigurationError,
)
from orb.providers.azure.infrastructure.credential_factory import (
    AsyncAzureCredentialProtocol,
    create_default_azure_credential_async,
    format_import_error,
)
from orb.providers.azure.infrastructure.services.arm_resource_id_parser import (
    ArmResourceIdParser,
    ParsedArmResourceId,
)
from orb.providers.azure.infrastructure.services.azure_network_identity_resolver import (
    AzureNetworkIdentity,
    AzureNetworkIdentityResolver,
)

if TYPE_CHECKING:
    from azure.mgmt.compute.aio import ComputeManagementClient as AsyncComputeManagementClient
    from azure.mgmt.network.aio import NetworkManagementClient as AsyncNetworkManagementClient
    from azure.mgmt.resource.resources.aio import (
        ResourceManagementClient as AsyncResourceManagementClient,
    )
    from azure.mgmt.resource.subscriptions.aio import SubscriptionClient as AsyncSubscriptionClient


@dataclass(frozen=True)
class AzureClientRuntimeConfig:
    """Infrastructure-owned config resolved before AzureClient construction."""

    azure_config: AzureProviderConfig
    performance_config: PerformanceConfig = field(
        default_factory=lambda: PerformanceConfig.model_validate({})
    )


@injectable
class AzureClient:
    """Wrapper for Azure API interactions.

    * Provider and shared runtime config are resolved before construction
      and passed in as a typed infrastructure object.
    * Azure SDK management clients are created **lazily** on first access
      through ``@property`` accessors, keeping startup cost near zero.
    """

    def __init__(
        self,
        runtime_config: AzureClientRuntimeConfig,
        logger: LoggingPort,
    ) -> None:
        """Initialise the Azure client wrapper.

        Args:
            runtime_config: Fully resolved Azure client runtime settings.
            logger: Logger for diagnostic and operational messages.
        """
        self._runtime_config = runtime_config
        self._logger = logger
        self._azure_config = runtime_config.azure_config

        self.region_name: str = (
            self._azure_config.region if self._azure_config.region else "eastus2"
        )
        self.subscription_id: Optional[str] = self._azure_config.subscription_id
        self.resource_group: Optional[str] = self._azure_config.resource_group

        self._logger.debug("Azure client region determined: %s", self.region_name)

        max_retries = self._resolve_int_config_value(
            field_name="max_retries",
            raw_value=self._azure_config.max_retries,
            default=3,
            minimum=0,
        )
        connect_timeout = self._resolve_int_config_value(
            field_name="connect_timeout",
            raw_value=self._azure_config.connect_timeout,
            default=30,
            minimum=1,
        )
        read_timeout = self._resolve_int_config_value(
            field_name="read_timeout",
            raw_value=self._azure_config.read_timeout,
            default=60,
            minimum=1,
        )

        self._max_retries = max_retries
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout

        self.perf_config = self._map_performance_config(runtime_config.performance_config)

        # Lazy Azure SDK client slots
        self._async_credential: Optional[AsyncAzureCredentialProtocol] = None
        self._async_compute_client: Optional[AsyncComputeManagementClient] = None
        self._async_network_client: Optional[AsyncNetworkManagementClient] = None
        self._async_resource_client: Optional[AsyncResourceManagementClient] = None
        self._async_subscription_client: Optional[AsyncSubscriptionClient] = None
        self._credentials_validated = False
        self._closed = False
        self._pending_async_close_task: asyncio.Task[None] | None = None
        self._lazy_init_lock = RLock()
        self._arm_resource_id_parser = ArmResourceIdParser()
        self._network_identity_resolver = AzureNetworkIdentityResolver(
            async_network_client_getter=self.get_async_network_client,
            logger=logger,
            arm_resource_id_parser=self._arm_resource_id_parser,
            network_lookup_error_types=self._network_lookup_error_types,
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

    def get_provider_config(self) -> Optional[AzureProviderConfig]:
        """Return the active Azure provider configuration, if available."""
        return self._azure_config

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

    async def _async_management_client_credential(self) -> Any:
        """Return the async credential object to hand to async Azure SDK client constructors."""
        return await self.get_async_credential()

    async def _async_management_client_init_kwargs(
        self,
        *,
        requires_subscription_id: bool,
    ) -> dict[str, Any]:
        """Build constructor kwargs shared by async Azure management clients."""
        client_kwargs = {
            "credential": await self._async_management_client_credential(),
            **self._management_client_kwargs(),
        }
        if requires_subscription_id:
            self._ensure_subscription_id()
            client_kwargs["subscription_id"] = self.subscription_id
        return client_kwargs

    async def _build_management_client_async(
        self,
        *,
        loader: Callable[[], Any],
        client_name: str,
        missing_package_message: str,
        requires_subscription_id: bool,
    ) -> Any:
        """Construct a lazily imported async Azure SDK management client."""
        self._ensure_open()
        self._logger.debug("Initialising async %s on first use", client_name)
        try:
            client_class = loader()
        except ImportError as exc:
            raise AzureConfigurationError(missing_package_message) from exc
        return client_class(
            **await self._async_management_client_init_kwargs(
                requires_subscription_id=requires_subscription_id
            )
        )

    async def get_async_credential(self) -> AsyncAzureCredentialProtocol:
        """Return an async Azure credential, creating it on first use."""
        with self._lazy_init_lock:
            self._ensure_open()
            existing = self._async_credential
        if existing is not None:
            return existing
        self._logger.debug("Creating async Azure credential on first use")
        try:
            created = create_default_azure_credential_async(
                client_id=self._azure_config.client_id if self._azure_config else None,
                logger=self._logger,
            )
        except ImportError as exc:
            raise AuthenticationError(format_import_error("azure-identity", exc)) from exc
        with self._lazy_init_lock:
            self._ensure_open()
            if self._async_credential is None:
                self._async_credential = created
                return created
            existing = self._async_credential
        await self._close_async_resource("async_credential", created)
        return existing

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
                """Invoke a close function, logging and collecting any errors."""
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

            async_resources_present = any(
                resource is not None
                for resource in (
                    self._async_subscription_client,
                    self._async_resource_client,
                    self._async_network_client,
                    self._async_compute_client,
                    self._async_credential,
                )
            )

            self._credentials_validated = False
            self._closed = True

        if async_resources_present:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self.aclose())
            else:
                close_task = loop.create_task(self.aclose())
                with self._lazy_init_lock:
                    self._pending_async_close_task = close_task

                def _log_async_close_completion(task: asyncio.Task[None]) -> None:
                    with self._lazy_init_lock:
                        if self._pending_async_close_task is task:
                            self._pending_async_close_task = None
                    try:
                        task.result()
                    except Exception as exc:
                        self._logger.warning(
                            "Failed closing async Azure resources after AzureClient.close(): %s",
                            exc,
                        )

                close_task.add_done_callback(_log_async_close_completion)

        if close_errors:
            raise close_errors[0]

    async def aclose(self) -> None:
        """Close owned async Azure SDK resources."""
        close_errors: list[Exception] = []

        with self._lazy_init_lock:
            self._credentials_validated = False
            self._closed = True
            async_subscription_client = self._async_subscription_client
            self._async_subscription_client = None
            async_resource_client = self._async_resource_client
            self._async_resource_client = None
            async_network_client = self._async_network_client
            self._async_network_client = None
            async_compute_client = self._async_compute_client
            self._async_compute_client = None
            async_credential = self._async_credential
            self._async_credential = None

        for resource_name, resource in (
            ("async_subscription_client", async_subscription_client),
            ("async_resource_client", async_resource_client),
            ("async_network_client", async_network_client),
            ("async_compute_client", async_compute_client),
            ("async_credential", async_credential),
        ):
            if resource is None:
                continue
            try:
                await self._close_async_resource(resource_name, resource)
            except Exception as exc:
                close_errors.append(exc)
                self._logger.warning(
                    "Failed closing Azure resource %s: %s",
                    resource_name,
                    exc,
                )

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

    async def get_async_compute_client(self) -> AsyncComputeManagementClient:
        """Lazy initialisation of the async Azure Compute management client."""
        with self._lazy_init_lock:
            self._ensure_open()
            existing = self._async_compute_client
        if existing is not None:
            return existing

        created = await self._build_management_client_async(
            loader=self._load_async_compute_management_client,
            client_name="ComputeManagementClient",
            missing_package_message="azure-mgmt-compute package is not installed",
            requires_subscription_id=True,
        )
        with self._lazy_init_lock:
            self._ensure_open()
            if self._async_compute_client is None:
                self._async_compute_client = created
                return created
            surviving_client = self._async_compute_client
        await self._close_async_resource("async_compute_client", created)
        if surviving_client is None:
            raise RuntimeError("ComputeManagementClient disappeared during async initialization")
        return surviving_client

    async def get_async_network_client(self) -> AsyncNetworkManagementClient:
        """Lazy initialisation of the async Azure Network management client."""
        with self._lazy_init_lock:
            self._ensure_open()
            existing = self._async_network_client
        if existing is not None:
            return existing

        created = await self._build_management_client_async(
            loader=self._load_async_network_management_client,
            client_name="NetworkManagementClient",
            missing_package_message="azure-mgmt-network package is not installed",
            requires_subscription_id=True,
        )
        with self._lazy_init_lock:
            self._ensure_open()
            if self._async_network_client is None:
                self._async_network_client = created
                return created
            surviving_client = self._async_network_client
        await self._close_async_resource("async_network_client", created)
        if surviving_client is None:
            raise RuntimeError("NetworkManagementClient disappeared during async initialization")
        return surviving_client

    async def get_async_resource_client(self) -> AsyncResourceManagementClient:
        """Lazy initialisation of the async Azure Resource management client."""
        with self._lazy_init_lock:
            self._ensure_open()
            existing = self._async_resource_client
        if existing is not None:
            return existing

        created = await self._build_management_client_async(
            loader=self._load_async_resource_management_client,
            client_name="ResourceManagementClient",
            missing_package_message="azure-mgmt-resource package is not installed",
            requires_subscription_id=True,
        )
        with self._lazy_init_lock:
            self._ensure_open()
            if self._async_resource_client is None:
                self._async_resource_client = created
                return created
            surviving_client = self._async_resource_client
        await self._close_async_resource("async_resource_client", created)
        if surviving_client is None:
            raise RuntimeError("ResourceManagementClient disappeared during async initialization")
        return surviving_client

    async def get_async_subscription_client(self) -> AsyncSubscriptionClient:
        """Lazy initialisation of the async Azure Subscription client."""
        with self._lazy_init_lock:
            self._ensure_open()
            existing = self._async_subscription_client
        if existing is not None:
            return existing

        created = await self._build_management_client_async(
            loader=self._load_async_subscription_client_class,
            client_name="SubscriptionClient",
            missing_package_message="azure-mgmt-resource package is not installed",
            requires_subscription_id=False,
        )
        with self._lazy_init_lock:
            self._ensure_open()
            if self._async_subscription_client is None:
                self._async_subscription_client = created
                return created
            surviving_client = self._async_subscription_client
        await self._close_async_resource("async_subscription_client", created)
        if surviving_client is None:
            raise RuntimeError("SubscriptionClient disappeared during async initialization")
        return surviving_client

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    async def validate_credentials_async(self) -> bool:
        """Async variant of credential validation using the async Azure credential."""
        if self._credentials_validated:
            return True

        try:
            credential = await self.get_async_credential()
            await credential.get_token("https://management.azure.com/.default")
            self._credentials_validated = True
            self._logger.info("Azure credentials validated successfully")
            return True
        except self._credential_validation_error_types() as exc:
            self._logger.error("Azure credential validation failed: %s", exc)
            return False

    async def validate_subscription_async(self) -> bool:
        """Async variant of subscription validation using the async Azure SDK client."""
        if not self.subscription_id:
            self._logger.error("No subscription_id configured")
            return False

        try:
            subscription_client = await self.get_async_subscription_client()
            sub = await subscription_client.subscriptions.get(self.subscription_id)
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
    def _collect_error_types(
        *base_error_types: type[BaseException],
        optional_error_loaders: tuple[Callable[[], Any], ...],
    ) -> tuple[type[BaseException], ...]:
        """Build a de-duplicated exception tuple with optional lazy imports."""
        error_types: list[type[BaseException]] = list(base_error_types)
        for loader in optional_error_loaders:
            try:
                error_type = loader()
            except ImportError:
                continue
            error_types.append(error_type)
        return tuple(dict.fromkeys(error_types))

    @classmethod
    def _credential_validation_error_types(cls) -> tuple[type[BaseException], ...]:
        """Return errors that should result in a soft credential validation failure."""
        return cls._collect_error_types(
            AuthenticationError,
            optional_error_loaders=(
                cls._load_azure_error_type,
                cls._load_client_authentication_error_type,
            ),
        )

    @classmethod
    def _subscription_validation_error_types(cls) -> tuple[type[BaseException], ...]:
        """Return errors that should result in a soft subscription validation failure."""
        return cls._collect_error_types(
            AzureConfigurationError,
            *cls._credential_validation_error_types(),
            optional_error_loaders=(),
        )

    @classmethod
    def _network_lookup_error_types(cls) -> tuple[type[BaseException], ...]:
        """Return Azure/network lookup errors that should not abort status enrichment."""
        return cls._collect_error_types(
            AzureConfigurationError,
            optional_error_loaders=(cls._load_azure_error_type,),
        )

    def _get_network_identity_resolver(self) -> AzureNetworkIdentityResolver:
        """Return the initialized network identity resolver."""
        return self._network_identity_resolver

    async def _close_async_resource(self, resource_name: str, resource: Any) -> None:
        """Close one async Azure SDK resource owned by this wrapper."""
        try:
            await resource.close()
        except Exception:
            raise
        self._logger.debug("Closed Azure resource %s", resource_name)

    @staticmethod
    def _load_async_compute_management_client() -> Any:
        from azure.mgmt.compute.aio import ComputeManagementClient

        return ComputeManagementClient

    @staticmethod
    def _load_async_network_management_client() -> Any:
        from azure.mgmt.network.aio import NetworkManagementClient

        return NetworkManagementClient

    @staticmethod
    def _load_async_resource_management_client() -> Any:
        from azure.mgmt.resource.resources.aio import ResourceManagementClient

        return ResourceManagementClient

    @staticmethod
    def _load_async_subscription_client_class() -> Any:
        from azure.mgmt.resource.subscriptions.aio import SubscriptionClient

        return SubscriptionClient

    @staticmethod
    def _load_azure_error_type() -> Any:
        from azure.core.exceptions import AzureError

        return AzureError

    @staticmethod
    def _load_client_authentication_error_type() -> Any:
        from azure.core.exceptions import ClientAuthenticationError

        return ClientAuthenticationError

    @classmethod
    def _parse_arm_resource_id(
        cls,
        arm_id: str,
    ) -> Optional[ParsedArmResourceId]:
        """Parse an ARM resource ID only when it matches the canonical shape."""
        return ArmResourceIdParser.parse(arm_id)

    @classmethod
    def extract_resource_group_and_name_from_arm_id(
        cls,
        arm_id: str,
    ) -> Optional[tuple[str, str]]:
        """Extract ``(resource_group, resource_name)`` from an ARM resource ID."""
        return ArmResourceIdParser().extract_resource_group_and_name(arm_id)

    @classmethod
    def subnet_id_to_vnet_id(cls, subnet_id: Optional[str]) -> Optional[str]:
        """Return the parent VNet ARM ID from a subnet ARM ID."""
        return ArmResourceIdParser().subnet_id_to_vnet_id(subnet_id)

    async def resolve_network_identity_from_vm_async(self, vm: Any) -> AzureNetworkIdentity:
        """Async variant of VM network identity resolution."""
        return await self._get_network_identity_resolver().resolve_from_vm_async(vm)

    async def resolve_network_identity_from_nic_refs_async(
        self,
        nic_refs: list[Any],
    ) -> AzureNetworkIdentity:
        """Async variant of NIC-ref network identity resolution."""
        return await self._get_network_identity_resolver().resolve_from_nic_refs_async(nic_refs)
