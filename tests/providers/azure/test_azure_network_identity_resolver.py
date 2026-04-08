"""Focused tests for ARM ID parsing and Azure network identity resolution."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.providers.azure.infrastructure.services.arm_resource_id_parser import (
    ArmResourceIdParser,
)
from orb.providers.azure.infrastructure.services.azure_network_identity_resolver import (
    AzureNetworkIdentityResolver,
)


class TestArmResourceIdParser:
    def test_extract_resource_group_and_name_rejects_incomplete_ids(self):
        parser = ArmResourceIdParser()

        assert (
            parser.extract_resource_group_and_name("/subscriptions/sub/resourceGroups")
            is None
        )

    def test_extract_resource_group_and_name_rejects_malformed_ids(self):
        parser = ArmResourceIdParser()

        assert (
            parser.extract_resource_group_and_name(
                "/subscriptions/sub/resourceGroups/test-rg/providers/"
                "Microsoft.Network/networkInterfaces//nic-vm-1"
            )
            is None
        )
        assert (
            parser.extract_resource_group_and_name(
                "/subscriptions/sub/resourceGroups/test-rg/providers/"
                "Microsoft.Network/networkInterfaces"
            )
            is None
        )

    def test_extract_resource_group_and_name_accepts_child_resources(self):
        parser = ArmResourceIdParser()

        assert parser.extract_resource_group_and_name(
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/virtualNetworks/test-vnet/subnets/default"
        ) == ("test-rg", "default")

    def test_subnet_id_to_vnet_id_rejects_malformed_or_non_subnet_ids(self):
        parser = ArmResourceIdParser()

        assert (
            parser.subnet_id_to_vnet_id(
                "/subscriptions/sub/resourceGroups/test-rg/providers/"
                "Microsoft.Network/virtualNetworks/test-vnet/subnets"
            )
            is None
        )
        assert (
            parser.subnet_id_to_vnet_id(
                "/subscriptions/sub/resourceGroups/test-rg/providers/"
                "Microsoft.Network/networkInterfaces/nic-vm-1"
            )
            is None
        )


class TestAzureNetworkIdentityResolver:
    @staticmethod
    def _build_resolver() -> tuple[AzureNetworkIdentityResolver, MagicMock]:
        network_client = MagicMock()
        resolver = AzureNetworkIdentityResolver(
            network_client_getter=lambda: network_client,
            logger=MagicMock(),
            arm_resource_id_parser=ArmResourceIdParser(),
            network_lookup_error_types=lambda: (),
        )
        return resolver, network_client

    def test_resolve_from_vm_populates_ips_and_subnet(self):
        resolver, network_client = self._build_resolver()

        nic_ref = MagicMock()
        nic_ref.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/networkInterfaces/nic-vm-1"
        )
        nic_ref.properties.primary = True

        vm = MagicMock()
        vm.network_profile.network_interfaces = [nic_ref]

        subnet = MagicMock()
        subnet.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/virtualNetworks/test-vnet/subnets/default"
        )
        public_ip_ref = MagicMock()
        public_ip_ref.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/publicIPAddresses/pip-vm-1"
        )
        ip_cfg = MagicMock()
        ip_cfg.private_ip_address = "10.0.0.4"
        ip_cfg.subnet = subnet
        ip_cfg.public_ip_address = public_ip_ref

        nic = MagicMock()
        nic.ip_configurations = [ip_cfg]
        network_client.network_interfaces.get.return_value = nic

        pip = MagicMock()
        pip.ip_address = "52.1.2.3"
        network_client.public_ip_addresses.get.return_value = pip

        result = resolver.resolve_from_vm(vm)

        assert result["private_ip"] == "10.0.0.4"
        assert result["public_ip"] == "52.1.2.3"
        assert result["subnet_id"].endswith("/subnets/default")
        assert result["vnet_id"].endswith("/virtualNetworks/test-vnet")
        assert result["nic_name"] == "nic-vm-1"

    def test_resolve_from_vm_tolerates_missing_nested_property_bags(self):
        resolver, network_client = self._build_resolver()

        nic_ref = MagicMock()
        nic_ref.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/networkInterfaces/nic-vm-1"
        )
        nic_ref.properties = None

        vm = MagicMock()
        vm.network_profile.network_interfaces = [nic_ref]

        subnet = MagicMock()
        subnet.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/virtualNetworks/test-vnet/subnets/default"
        )
        public_ip_ref = MagicMock()
        public_ip_ref.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/publicIPAddresses/pip-vm-1"
        )
        ip_cfg = MagicMock()
        ip_cfg.private_ip_address = "10.0.0.4"
        ip_cfg.subnet = subnet
        ip_cfg.public_ip_address = public_ip_ref
        ip_cfg.properties = None

        nic = MagicMock()
        nic.ip_configurations = [ip_cfg]
        network_client.network_interfaces.get.return_value = nic

        pip = MagicMock()
        pip.ip_address = "52.1.2.3"
        network_client.public_ip_addresses.get.return_value = pip

        result = resolver.resolve_from_vm(vm)

        assert result["private_ip"] == "10.0.0.4"
        assert result["public_ip"] == "52.1.2.3"
        assert result["subnet_id"].endswith("/subnets/default")
        assert result["vnet_id"].endswith("/virtualNetworks/test-vnet")
        assert result["nic_name"] == "nic-vm-1"

    def test_resolve_from_nic_refs_skips_known_nic_lookup_errors(self):
        from azure.core.exceptions import ResourceNotFoundError

        network_client = MagicMock()
        resolver = AzureNetworkIdentityResolver(
            network_client_getter=lambda: network_client,
            logger=MagicMock(),
            arm_resource_id_parser=ArmResourceIdParser(),
            network_lookup_error_types=lambda: (ResourceNotFoundError,),
        )

        nic_ref = MagicMock()
        nic_ref.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/networkInterfaces/nic-vm-1"
        )
        nic_ref.properties.primary = True
        network_client.network_interfaces.get.side_effect = ResourceNotFoundError("missing")

        assert resolver.resolve_from_nic_refs([nic_ref]) == {
            "private_ip": None,
            "public_ip": None,
            "subnet_id": None,
            "vnet_id": None,
            "nic_id": None,
            "nic_name": None,
        }

    def test_resolve_from_nic_refs_reraises_unexpected_nic_lookup_errors(self):
        resolver, network_client = self._build_resolver()

        nic_ref = MagicMock()
        nic_ref.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/networkInterfaces/nic-vm-1"
        )
        nic_ref.properties.primary = True
        network_client.network_interfaces.get.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            resolver.resolve_from_nic_refs([nic_ref])
