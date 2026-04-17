"""Tests for the Azure domain template aggregate, value objects, and ARM mapper."""

import pytest
from pydantic import ValidationError

from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import (
    AzureAllocationStrategy,
    AzureDataDisk,
    AzureEvictionPolicy,
    AzureImageReference,
    AzureNetworkConfig,
    AzureOSDiskConfig,
    AzureOSDiskType,
    AzurePriority,
    AzureProviderApi,
    AzureUpgradePolicyMode,
    AzureVMSSOrchestrationMode,
)
from orb.providers.azure.infrastructure.services.arm_payload_mapper import ArmPayloadMapper


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------

_BASE_FIELDS = {
    "template_id": "test-template",
    "vm_size": "Standard_D4s_v5",
    "resource_group": "test-rg",
    "location": "eastus2",
    "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
    "image": {
        "publisher": "Canonical",
        "offer": "0001-com-ubuntu-server-jammy",
        "sku": "22_04-lts-gen2",
        "version": "latest",
    },
}

_SPOT_DEFAULTS = {
    "priority": "Spot",
    "eviction_policy": "Deallocate",
}


# ---------------------------------------------------------------------------
# AzureTemplate basic construction
# ---------------------------------------------------------------------------


class TestAzureTemplateConstruction:
    def test_minimal_template(self):
        t = AzureTemplate(**_BASE_FIELDS)
        assert t.template_id == "test-template"
        assert t.vm_size == "Standard_D4s_v5"
        assert t.resource_group.value == "test-rg"
        assert t.location.value == "eastus2"
        assert t.provider_api == AzureProviderApi.VMSS

    def test_rejects_missing_ssh_keys(self):
        """SSH access is required — no password fallback (mirrors AWS key_name pattern)."""
        fields = {**_BASE_FIELDS}
        fields.pop("ssh_public_keys", None)
        with pytest.raises(ValueError, match="SSH access is required"):
            AzureTemplate(**fields)

    def test_rejects_empty_ssh_keys_without_key_name(self):
        """Empty ssh_public_keys without ssh_key_name should be rejected."""
        fields = {**_BASE_FIELDS, "ssh_public_keys": []}
        with pytest.raises(ValueError, match="SSH access is required"):
            AzureTemplate(**fields)

    def test_rejects_missing_image_source(self):
        fields = {**_BASE_FIELDS}
        fields.pop("image")
        with pytest.raises(ValueError, match="image source is required"):
            AzureTemplate(**fields)

    def test_ssh_key_name_accepted(self):
        """ssh_key_name alone (without inline keys) should pass validation."""
        fields = {
            "template_id": "test-template",
            "vm_size": "Standard_D4s_v5",
            "resource_group": "test-rg",
            "location": "eastus2",
            "ssh_key_name": "my-azure-ssh-key",
            "image": {
                "publisher": "Canonical",
                "offer": "0001-com-ubuntu-server-jammy",
                "sku": "22_04-lts-gen2",
                "version": "latest",
            },
        }
        t = AzureTemplate(**fields)
        assert t.ssh_key_name == "my-azure-ssh-key"

    def test_with_custom_image_id(self):
        fields = {**_BASE_FIELDS}
        fields.pop("image", None)
        t = AzureTemplate(
            **fields,
            image={"image_id": "/subscriptions/.../images/my-image"},
        )
        assert t.image.image_id == "/subscriptions/.../images/my-image"

    def test_rejects_invalid_location_slug(self):
        fields = {**_BASE_FIELDS, "location": "East US 2"}
        with pytest.raises(ValueError, match="location must be an Azure location slug"):
            AzureTemplate(**fields)

    def test_rejects_invalid_arm_identifier_type(self):
        with pytest.raises(ValueError, match="diskEncryptionSets"):
            AzureTemplate(
                **_BASE_FIELDS,
                disk_encryption_set_id=(
                    "/subscriptions/sub/resourceGroups/rg/providers/"
                    "Microsoft.Compute/proximityPlacementGroups/ppg-1"
                ),
            )

    def test_accepts_mixed_case_arm_identifier_segments(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            disk_encryption_set_id=(
                "/SUBSCRIPTIONS/sub/RESOURCEGROUPS/rg/PROVIDERS/"
                "MICROSOFT.COMPUTE/DISKENCRYPTIONSETS/des-1"
            ),
        )
        assert (
            str(t.disk_encryption_set_id)
            == "/SUBSCRIPTIONS/sub/RESOURCEGROUPS/rg/PROVIDERS/"
            "MICROSOFT.COMPUTE/DISKENCRYPTIONSETS/des-1"
        )

    def test_rejects_invalid_upgrade_policy_mode(self):
        with pytest.raises(ValueError, match="upgrade_policy_mode"):
            AzureTemplate(**_BASE_FIELDS, upgrade_policy_mode="BlueGreen")

    def test_model_dump_json_serialises_azure_value_objects_as_strings(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            proximity_placement_group_id=(
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.Compute/proximityPlacementGroups/ppg-1"
            ),
            upgrade_policy_mode="Rolling",
        )

        payload = t.model_dump(mode="json", exclude_none=True)

        assert payload["resource_group"] == "test-rg"
        assert payload["location"] == "eastus2"
        assert (
            payload["proximity_placement_group_id"]
            == "/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.Compute/proximityPlacementGroups/ppg-1"
        )
        assert payload["upgrade_policy_mode"] == "Rolling"

    def test_vmss_uniform_rejects_flexible_orchestration_mode(self):
        with pytest.raises(ValueError, match="VMSSUniform"):
            AzureTemplate(
                **_BASE_FIELDS,
                provider_api=AzureProviderApi.VMSS_UNIFORM,
            )

    def test_vmss_uniform_accepts_uniform_orchestration_mode(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            provider_api=AzureProviderApi.VMSS_UNIFORM,
            orchestration_mode=AzureVMSSOrchestrationMode.UNIFORM,
        )
        assert t.provider_api == AzureProviderApi.VMSS_UNIFORM
        assert t.orchestration_mode == AzureVMSSOrchestrationMode.UNIFORM

    def test_provider_type_forced_to_azure(self):
        t = AzureTemplate(**_BASE_FIELDS, provider_type="wrong")
        assert t.provider_type == "azure"

    def test_template_is_frozen_after_construction(self):
        template = AzureTemplate(**_BASE_FIELDS)

        with pytest.raises(ValidationError):
            template.vm_size = "Standard_D8s_v5"

    def test_from_azure_format(self):
        data = {
            **_BASE_FIELDS,
            "vmSize": "Standard_D2s_v5",
        }
        data.pop("vm_size")
        t = AzureTemplate.from_azure_format(data)
        assert t.vm_size == "Standard_D2s_v5"

    def test_from_azure_format_accepts_max_number(self):
        data = {
            **_BASE_FIELDS,
            "max_number": 7,
        }
        t = AzureTemplate.from_azure_format(data)
        assert t.max_instances == 7

    def test_from_azure_format_prefers_explicit_max_instances_over_max_number(self):
        data = {
            **_BASE_FIELDS,
            "max_instances": 3,
            "max_number": 7,
        }
        t = AzureTemplate.from_azure_format(data)
        assert t.max_instances == 3

    def test_rejects_both_provider_api_spec_and_file(self):
        with pytest.raises(ValueError, match="provider_api_spec and provider_api_spec_file"):
            AzureTemplate(
                **_BASE_FIELDS,
                provider_api_spec={"location": "eastus2"},
                provider_api_spec_file="vmss.json",
            )


# ---------------------------------------------------------------------------
# Spot / priority validation
# ---------------------------------------------------------------------------


class TestSpotValidation:
    def test_spot_defaults_eviction_policy(self):
        """mode='before' validator fills in implied defaults for Spot."""
        t = AzureTemplate(**_BASE_FIELDS, priority="Spot")
        assert t.eviction_policy == AzureEvictionPolicy.DEALLOCATE

    def test_spot_accepts_explicit_values(self):
        t = AzureTemplate(**_BASE_FIELDS, **_SPOT_DEFAULTS)
        assert t.eviction_policy == AzureEvictionPolicy.DEALLOCATE

    def test_spot_percentage_implies_spot_priority(self):
        t = AzureTemplate(**_BASE_FIELDS, spot_percentage=70)
        assert t.priority == AzurePriority.SPOT
        assert t.eviction_policy == AzureEvictionPolicy.DEALLOCATE

    def test_spot_percentage_requires_flexible(self):
        with pytest.raises(ValueError, match="Flexible orchestration mode"):
            AzureTemplate(
                **_BASE_FIELDS,
                spot_percentage=70,
                orchestration_mode=AzureVMSSOrchestrationMode.UNIFORM,
            )

    def test_spot_percentage_rejects_single_placement_group(self):
        with pytest.raises(ValueError, match="single_placement_group"):
            AzureTemplate(
                **_BASE_FIELDS,
                spot_percentage=70,
                single_placement_group=True,
            )

    def test_regular_rejects_eviction_policy(self):
        with pytest.raises(ValueError, match="eviction_policy"):
            AzureTemplate(**_BASE_FIELDS, priority="Regular", eviction_policy="Delete")

    def test_regular_rejects_billing_max_price(self):
        with pytest.raises(ValueError, match="billing_profile_max_price"):
            AzureTemplate(**_BASE_FIELDS, priority="Regular", billing_profile_max_price=1.0)


# ---------------------------------------------------------------------------
# Security validation
# ---------------------------------------------------------------------------


class TestSecurityValidation:
    def test_trusted_launch_defaults(self):
        """mode='before' validator fills in secure_boot and vtpm for TrustedLaunch."""
        t = AzureTemplate(**_BASE_FIELDS, security_type="TrustedLaunch")
        assert t.secure_boot_enabled is True
        assert t.vtpm_enabled is True

    def test_trusted_launch_accepts_explicit_values(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            security_type="TrustedLaunch",
            secure_boot_enabled=True,
            vtpm_enabled=True,
        )
        assert t.secure_boot_enabled is True
        assert t.vtpm_enabled is True


# ---------------------------------------------------------------------------
# Zone validation
# ---------------------------------------------------------------------------


class TestZoneValidation:
    def test_zone_balance_requires_zones(self):
        with pytest.raises(ValueError, match="zone_balance"):
            AzureTemplate(**_BASE_FIELDS, zone_balance=True)

    def test_zone_balance_with_zones(self):
        t = AzureTemplate(**_BASE_FIELDS, zone_balance=True, zones=["1", "2"])
        assert t.zone_balance is True

    def test_overprovision_rejected_for_flexible(self):
        with pytest.raises(ValueError, match="overprovision"):
            AzureTemplate(
                **_BASE_FIELDS,
                orchestration_mode=AzureVMSSOrchestrationMode.FLEXIBLE,
                overprovision=True,
            )

    def test_overprovision_allowed_for_uniform(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            orchestration_mode=AzureVMSSOrchestrationMode.UNIFORM,
            overprovision=True,
        )
        assert t.overprovision is True


# ---------------------------------------------------------------------------
# ARM payload generation
# ---------------------------------------------------------------------------


class TestArmPayload:
    def test_basic_arm_payload(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            network_config={"subnet_id": "/subscriptions/.../subnets/default"},
        )
        arm = ArmPayloadMapper.vmss_payload(t)

        assert arm["type"] == "Microsoft.Compute/virtualMachineScaleSets"
        assert arm["location"] == "eastus2"
        assert arm["sku"]["name"] == "Standard_D4s_v5"
        assert "virtualMachineProfile" in arm["properties"]
        vm_profile = arm["properties"]["virtualMachineProfile"]
        assert vm_profile["storageProfile"]["osDisk"]["deleteOption"] == "Delete"
        nic_config = vm_profile["networkProfile"]["networkInterfaceConfigurations"][0]
        assert nic_config["properties"]["deleteOption"] == "Delete"

    def test_spot_arm_payload(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            priority="Spot",
            billing_profile_max_price=-1.0,
        )
        arm = ArmPayloadMapper.vmss_payload(t)
        vm_profile = arm["properties"]["virtualMachineProfile"]
        assert vm_profile["priority"] == "Spot"
        assert vm_profile["billingProfile"]["maxPrice"] == -1.0

    def test_vmss_mix_payload_uses_sku_profile(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            vm_sizes=["Standard_D8s_v5", "Standard_D16s_v5"],
            vmss_allocation_strategy=AzureAllocationStrategy.CAPACITY_OPTIMIZED,
        )
        arm = ArmPayloadMapper.vmss_payload(t)

        assert arm["sku"]["name"] == "Mix"
        assert arm["skuProfile"]["vmSizes"] == [
            {"name": "Standard_D4s_v5"},
            {"name": "Standard_D8s_v5"},
            {"name": "Standard_D16s_v5"},
        ]
        assert arm["skuProfile"]["allocationStrategy"] == "CapacityOptimized"
        assert "vmSizeProperties" not in arm["properties"]["virtualMachineProfile"]["hardwareProfile"]

    def test_vmss_prioritized_mix_includes_ranks(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            vm_size_preferences=[
                {"name": "Standard_D8s_v5", "rank": 2},
                {"name": "Standard_D16s_v5"},
            ],
            vmss_allocation_strategy=AzureAllocationStrategy.PRIORITIZED,
        )

        arm = ArmPayloadMapper.vmss_payload(t)

        assert arm["sku"]["name"] == "Mix"
        assert arm["skuProfile"]["allocationStrategy"] == "Prioritized"
        assert arm["skuProfile"]["vmSizes"] == [
            {"name": "Standard_D4s_v5", "rank": 0},
            {"name": "Standard_D8s_v5", "rank": 2},
            {"name": "Standard_D16s_v5", "rank": 1},
        ]

    def test_prioritized_mix_requires_ranked_preferences(self):
        with pytest.raises(ValueError, match="requires at least one vm_size_preference"):
            AzureTemplate(
                **_BASE_FIELDS,
                vm_sizes=["Standard_D8s_v5", "Standard_D16s_v5"],
                vmss_allocation_strategy=AzureAllocationStrategy.PRIORITIZED,
            )

    def test_vm_size_preferences_are_rank_only(self):
        with pytest.raises(ValueError, match="vm_size_preferences is only valid"):
            AzureTemplate(
                **_BASE_FIELDS,
                vm_size_preferences=[{"name": "Standard_D8s_v5"}],
                vmss_allocation_strategy=AzureAllocationStrategy.CAPACITY_OPTIMIZED,
            )

    def test_vm_sizes_and_vm_size_preferences_are_mutually_exclusive(self):
        with pytest.raises(ValueError, match="Specify either vm_sizes or vm_size_preferences"):
            AzureTemplate(
                **_BASE_FIELDS,
                vm_sizes=["Standard_D8s_v5"],
                vm_size_preferences=[{"name": "Standard_D16s_v5", "rank": 1}],
                vmss_allocation_strategy=AzureAllocationStrategy.PRIORITIZED,
            )

    def test_vmss_mix_without_vmss_allocation_strategy_omits_allocation_strategy(self):
        t = AzureTemplate(**_BASE_FIELDS, priority="Spot", vm_sizes=["Standard_D8s_v5"])

        arm = ArmPayloadMapper.vmss_payload(t)

        assert "allocationStrategy" not in arm["skuProfile"]

    def test_spot_percentage_populates_priority_mix_policy(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            vm_sizes=["Standard_D8s_v5"],
            priority="Spot",
            spot_percentage=70,
            base_regular_priority_count=2,
        )
        arm = ArmPayloadMapper.vmss_payload(t)

        vm_profile = arm["properties"]["virtualMachineProfile"]
        assert vm_profile["priority"] == "Spot"
        assert arm["properties"]["priorityMixPolicy"] == {
            "baseRegularPriorityCount": 2,
            "regularPriorityPercentageAboveBase": 30,
        }

    def test_zones_in_arm_payload(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            zones=["1", "2", "3"],
        )
        arm = ArmPayloadMapper.vmss_payload(t)
        assert arm["zones"] == ["1", "2", "3"]

    def test_identity_in_arm_payload(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            system_assigned_identity=True,
            user_assigned_identity_ids=["/subscriptions/.../identities/my-id"],
        )
        arm = ArmPayloadMapper.vmss_payload(t)
        assert arm["identity"]["type"] == "SystemAssigned, UserAssigned"

    def test_disk_encryption_set_is_applied_to_vmss_os_and_data_disks(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            disk_encryption_set_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/diskEncryptionSets/des-1",
            data_disks=[{"lun": 0, "disk_size_gb": 256}],
        )
        arm = ArmPayloadMapper.vmss_payload(t)
        storage_profile = arm["properties"]["virtualMachineProfile"]["storageProfile"]

        assert storage_profile["osDisk"]["managedDisk"]["diskEncryptionSet"] == {
            "id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/diskEncryptionSets/des-1"
        }
        assert storage_profile["dataDisks"][0]["managedDisk"]["diskEncryptionSet"] == {
            "id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/diskEncryptionSets/des-1"
        }

    def test_single_vm_payload_reuses_storage_profile_builder_with_single_vm_defaults(self):
        t = AzureTemplate(
            **_BASE_FIELDS,
            provider_api=AzureProviderApi.SINGLE_VM,
        )

        params = ArmPayloadMapper.single_vm_payload(
            template=t,
            vm_name="vm-test",
            nic_id="/subscriptions/.../networkInterfaces/nic-vm-test",
        )
        storage_profile = params["properties"]["storageProfile"]

        assert storage_profile["imageReference"]["publisher"] == "Canonical"
        assert storage_profile["osDisk"]["managedDisk"]["storageAccountType"] == "Standard_LRS"
        assert "caching" not in storage_profile["osDisk"]


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class TestValueObjects:
    def test_image_reference_marketplace(self):
        img = AzureImageReference(
            publisher="Canonical",
            offer="0001-com-ubuntu-server-jammy",
            sku="22_04-lts-gen2",
        )
        arm = img.to_arm_dict()
        assert arm["publisher"] == "Canonical"
        assert arm["version"] == "latest"

    def test_image_reference_custom(self):
        img = AzureImageReference(image_id="/subscriptions/.../images/myimg")
        arm = img.to_arm_dict()
        assert arm["id"] == "/subscriptions/.../images/myimg"

    def test_image_reference_requires_one_source(self):
        with pytest.raises(ValueError):
            AzureImageReference()

    def test_os_disk_config(self):
        disk = AzureOSDiskConfig(
            disk_size_gb=128,
            storage_account_type=AzureOSDiskType.PREMIUM_LRS,
        )
        arm = disk.to_arm_dict()
        assert arm["diskSizeGB"] == 128
        assert arm["deleteOption"] == "Delete"
        assert arm["managedDisk"]["storageAccountType"] == "Premium_LRS"

    def test_ephemeral_os_disk_defaults_placement(self):
        disk = AzureOSDiskConfig(ephemeral_os_disk=True)

        assert disk.ephemeral_placement == "CacheDisk"

    def test_data_disk(self):
        dd = AzureDataDisk(lun=0, disk_size_gb=256)
        arm = dd.to_arm_dict()
        assert arm["lun"] == 0
        assert arm["diskSizeGB"] == 256
        assert arm["createOption"] == "Empty"
        assert arm["deleteOption"] == "Delete"

    def test_network_config(self):
        nc = AzureNetworkConfig(
            subnet_id="/subscriptions/.../subnets/default",
            public_ip_enabled=True,
            load_balancer_backend_pool_ids=["/subscriptions/.../backendAddressPools/pool-a"],
            load_balancer_inbound_nat_pool_ids=["/subscriptions/.../inboundNatPools/nat-a"],
            application_gateway_backend_pool_ids=["/subscriptions/.../backendAddressPools/appgw-a"],
        )
        arm = nc.to_arm_dict()
        assert arm["properties"]["deleteOption"] == "Delete"
        assert arm["properties"]["primary"] is True
        ip_config = arm["properties"]["ipConfigurations"][0]["properties"]
        assert ip_config["subnet"]["id"] == (
            "/subscriptions/.../subnets/default"
        )
        assert ip_config["publicIPAddressConfiguration"]["properties"]["deleteOption"] == "Delete"
        assert ip_config["loadBalancerBackendAddressPools"][0]["id"].endswith("/pool-a")
        assert ip_config["loadBalancerInboundNatPools"][0]["id"].endswith("/nat-a")
        assert ip_config["applicationGatewayBackendAddressPools"][0]["id"].endswith("/appgw-a")

    def test_allocation_strategy_from_core(self):
        from orb.domain.base.value_objects import AllocationStrategy

        mapped = AzureAllocationStrategy.from_core(AllocationStrategy.LOWEST_PRICE)
        assert mapped == AzureAllocationStrategy.LOWEST_PRICE

    def test_priority_from_price_type(self):
        from orb.domain.base.value_objects import PriceType

        mapped = AzurePriority.from_price_type(PriceType.SPOT)
        assert mapped == AzurePriority.SPOT
