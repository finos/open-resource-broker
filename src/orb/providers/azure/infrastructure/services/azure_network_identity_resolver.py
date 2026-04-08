"""Azure VM/NIC network identity enrichment helpers."""

from __future__ import annotations

from typing import Any, Callable, Optional, Protocol, cast

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


class AzureNetworkIdentityResolver:
    """Resolve private/public IP and subnet/VNet identity from Azure NIC references."""

    def __init__(
        self,
        *,
        network_client_getter: Callable[[], Any],
        logger: LoggingPort,
        arm_resource_id_parser: ArmResourceIdParser,
        network_lookup_error_types: Callable[[], tuple[type[BaseException], ...]],
    ) -> None:
        self._network_client_getter = network_client_getter
        self._logger = logger
        self._arm_resource_id_parser = arm_resource_id_parser
        self._network_lookup_error_types = network_lookup_error_types

    @staticmethod
    def empty_network_identity() -> dict[str, Any]:
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

    @property
    def _network_client(self) -> Any:
        return self._network_client_getter()

    def resolve_from_vm(self, vm: Any) -> dict[str, Any]:
        """Resolve network identity fields from a VM or VMSS VM object."""
        net_profile = cast(AzureVmNetworkIdentityProtocol, vm).network_profile
        nic_refs = self._network_interface_refs_from_profile(net_profile)
        return self.resolve_from_nic_refs(nic_refs)

    def resolve_from_nic_refs(
        self,
        nic_refs: list[AzureNicReferenceProtocol],
    ) -> dict[str, Any]:
        """Resolve private/public IP and subnet/VNet identity from NIC refs."""
        network_identity = self.empty_network_identity()
        if not nic_refs:
            return network_identity

        ordered_refs = sorted(
            nic_refs,
            key=lambda ref: not self._is_primary_nic_ref(ref),
        )

        for nic_ref in ordered_refs:
            nic_id = nic_ref.id
            if not nic_id:
                continue

            nic_lookup = self._arm_resource_id_parser.extract_resource_group_and_name(
                str(nic_id)
            )
            if not nic_lookup:
                continue

            nic_rg, nic_name = nic_lookup
            try:
                nic = self._network_client.network_interfaces.get(
                    resource_group_name=nic_rg,
                    network_interface_name=nic_name,
                )
            except self._network_lookup_error_types() as exc:
                self._logger.debug("Failed to resolve NIC %s: %s", nic_id, exc)
                continue

            ip_configs = list(cast(AzureNicProtocol, nic).ip_configurations or [])
            for ip_cfg in ip_configs:
                private_ip = ip_cfg.private_ip_address
                if not private_ip and ip_cfg.properties is not None:
                    private_ip = ip_cfg.properties.private_ip_address
                subnet = self._subnet_from_ip_config(ip_cfg)
                subnet_id = subnet.id if subnet is not None else None
                public_ip_ref = self._public_ip_ref_from_ip_config(ip_cfg)

                public_ip = None
                public_ip_id = public_ip_ref.id if public_ip_ref is not None else None
                if public_ip_id:
                    public_ip_lookup = self._arm_resource_id_parser.extract_resource_group_and_name(
                        str(public_ip_id)
                    )
                    if public_ip_lookup:
                        pip_rg, pip_name = public_ip_lookup
                        try:
                            pip = self._network_client.public_ip_addresses.get(
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
                        "vnet_id": self._arm_resource_id_parser.subnet_id_to_vnet_id(subnet_id),
                        "nic_id": nic_id,
                        "nic_name": nic_name,
                    }
                )
                return network_identity

        return network_identity
