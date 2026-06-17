"""Azure VM/NIC network identity enrichment helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Protocol, TypedDict, cast

from orb.domain.base.ports import LoggingPort

from .arm_resource_id_parser import ArmResourceIdParser


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


class AzureNetworkIdentity(TypedDict):
    """Resolved network identity fields used by Azure status normalization."""

    private_ip: str | None
    public_ip: str | None
    subnet_id: str | None
    vnet_id: str | None
    nic_id: str | None
    nic_name: str | None


@dataclass(frozen=True)
class _AzureResourceLookup:
    """Parsed ARM lookup fields needed for Azure SDK get calls."""

    resource_id: str
    resource_group: str
    name: str


class AzureNetworkIdentityResolver:
    """Resolve private/public IP and subnet/VNet identity from Azure NIC references."""

    def __init__(
        self,
        *,
        async_network_client_getter: Callable[[], Awaitable[Any]],
        logger: LoggingPort,
        arm_resource_id_parser: ArmResourceIdParser,
        network_lookup_error_types: Callable[[], tuple[type[BaseException], ...]],
    ) -> None:
        self._async_network_client_getter = async_network_client_getter
        self._logger = logger
        self._arm_resource_id_parser = arm_resource_id_parser
        self._network_lookup_error_types = network_lookup_error_types

    @staticmethod
    def empty_network_identity() -> AzureNetworkIdentity:
        """Return the empty network identity shape used when enrichment fails."""
        return {
            "private_ip": None,
            "public_ip": None,
            "subnet_id": None,
            "vnet_id": None,
            "nic_id": None,
            "nic_name": None,
        }

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

    def _ordered_nic_refs(
        self,
        nic_refs: list[AzureNicReferenceProtocol],
    ) -> list[AzureNicReferenceProtocol]:
        """Return NIC refs ordered with the primary NIC first."""
        return sorted(
            nic_refs,
            key=lambda ref: not self._is_primary_nic_ref(ref),
        )

    def _nic_lookup_from_ref(
        self,
        nic_ref: AzureNicReferenceProtocol,
    ) -> _AzureResourceLookup | None:
        """Extract the NIC ARM ID, resource group, and resource name from a ref."""
        nic_id = nic_ref.id
        if not nic_id:
            return None

        nic_lookup = self._arm_resource_id_parser.extract_resource_group_and_name(
            str(nic_id)
        )
        if not nic_lookup:
            return None

        nic_rg, nic_name = nic_lookup
        return _AzureResourceLookup(
            resource_id=str(nic_id),
            resource_group=nic_rg,
            name=nic_name,
        )

    @staticmethod
    def _private_ip_from_ip_config(ip_config: AzureIpConfigurationProtocol) -> Optional[str]:
        """Resolve the private IP from direct fields or nested property bags."""
        private_ip = ip_config.private_ip_address
        if not private_ip and ip_config.properties is not None:
            private_ip = ip_config.properties.private_ip_address
        return private_ip

    def _build_network_identity(
        self,
        *,
        nic_id: str,
        nic_name: str,
        ip_config: AzureIpConfigurationProtocol,
        public_ip: Optional[str],
    ) -> AzureNetworkIdentity:
        """Build the resolved network identity payload for one NIC IP configuration."""
        subnet = self._subnet_from_ip_config(ip_config)
        subnet_id = subnet.id if subnet is not None else None
        return {
            "private_ip": self._private_ip_from_ip_config(ip_config),
            "public_ip": public_ip,
            "subnet_id": subnet_id,
            "vnet_id": self._arm_resource_id_parser.subnet_id_to_vnet_id(subnet_id),
            "nic_id": nic_id,
            "nic_name": nic_name,
        }

    def _public_ip_lookup_from_ip_config(
        self,
        ip_config: AzureIpConfigurationProtocol,
    ) -> _AzureResourceLookup | None:
        """Extract the public IP ARM ID, resource group, and resource name from an IP config."""
        public_ip_ref = self._public_ip_ref_from_ip_config(ip_config)
        public_ip_id = public_ip_ref.id if public_ip_ref is not None else None
        if not public_ip_id:
            return None

        public_ip_lookup = self._arm_resource_id_parser.extract_resource_group_and_name(
            str(public_ip_id)
        )
        if not public_ip_lookup:
            return None

        pip_rg, pip_name = public_ip_lookup
        return _AzureResourceLookup(
            resource_id=str(public_ip_id),
            resource_group=pip_rg,
            name=pip_name,
        )

    async def _resolve_public_ip_with_client_async(
        self,
        *,
        network_client: Any,
        ip_config: AzureIpConfigurationProtocol,
    ) -> Optional[str]:
        """Resolve a public IP address using the async Azure network client."""
        public_ip_lookup = self._public_ip_lookup_from_ip_config(ip_config)
        if public_ip_lookup is None:
            return None

        try:
            pip = await network_client.public_ip_addresses.get(
                resource_group_name=public_ip_lookup.resource_group,
                public_ip_address_name=public_ip_lookup.name,
            )
            return self._public_ip_address_from_resource(pip)
        except self._network_lookup_error_types() as exc:
            self._logger.debug(
                "Failed to resolve public IP %s: %s",
                public_ip_lookup.resource_id,
                exc,
            )
            return None

    async def _get_async_network_client(self) -> Any:
        return await self._async_network_client_getter()

    async def resolve_from_vm_async(self, vm: Any) -> AzureNetworkIdentity:
        """Async variant of ``resolve_from_vm`` using the async Network SDK."""
        net_profile = cast(AzureVmNetworkIdentityProtocol, vm).network_profile
        nic_refs = self._network_interface_refs_from_profile(net_profile)
        return await self.resolve_from_nic_refs_async(nic_refs)

    async def resolve_from_nic_refs_async(
        self,
        nic_refs: list[AzureNicReferenceProtocol],
    ) -> AzureNetworkIdentity:
        """Async variant of ``resolve_from_nic_refs`` using the async Network SDK."""
        network_identity = self.empty_network_identity()
        if not nic_refs:
            return network_identity

        ordered_refs = self._ordered_nic_refs(nic_refs)
        network_client = await self._get_async_network_client()

        for nic_ref in ordered_refs:
            nic_lookup = self._nic_lookup_from_ref(nic_ref)
            if nic_lookup is None:
                continue

            try:
                nic = await network_client.network_interfaces.get(
                    resource_group_name=nic_lookup.resource_group,
                    network_interface_name=nic_lookup.name,
                )
            except self._network_lookup_error_types() as exc:
                self._logger.debug(
                    "Failed to resolve NIC %s: %s",
                    nic_lookup.resource_id,
                    exc,
                )
                continue

            ip_configs = list(cast(AzureNicProtocol, nic).ip_configurations or [])
            for ip_cfg in ip_configs:
                network_identity.update(
                    self._build_network_identity(
                        nic_id=nic_lookup.resource_id,
                        nic_name=nic_lookup.name,
                        ip_config=ip_cfg,
                        public_ip=await self._resolve_public_ip_with_client_async(
                            network_client=network_client,
                            ip_config=ip_cfg,
                        ),
                    )
                )
                return network_identity

        return network_identity
