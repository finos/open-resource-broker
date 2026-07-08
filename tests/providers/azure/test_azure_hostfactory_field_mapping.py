"""Azure HostFactory field-mapping contracts."""

from orb.infrastructure.scheduler.hostfactory.field_mapper import HostFactoryFieldMapper
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.registration import register_azure_hostfactory_field_mapping


def test_azure_hostfactory_field_mapping_is_provider_owned():
    register_azure_hostfactory_field_mapping()

    mapped = HostFactoryFieldMapper("azure").map_input_fields(
        {
            "templateId": "azure-hf",
            "resourceGroup": "rg-test",
            "location": "uksouth",
            "vmType": "Standard_D4s_v5",
            "vmTypes": {"Standard_D4s_v5": 1, "Standard_D8s_v5": 1},
            "imageId": "Canonical:0001-com-ubuntu-server-jammy:22_04-lts:latest",
            "maxNumber": 2,
        }
    )

    assert mapped["template_id"] == "azure-hf"
    assert mapped["resource_group"] == "rg-test"
    assert mapped["vm_size"] == "Standard_D4s_v5"
    assert mapped["vm_sizes"] == {
        "Standard_D4s_v5": 1,
        "Standard_D8s_v5": 1,
    }


def test_azure_template_accepts_hostfactory_vm_types_mapping():
    template = AzureTemplate(
        template_id="azure-hf",
        resource_group="rg-test",
        location="uksouth",
        vm_size="Standard_D4s_v5",
        vm_sizes={"Standard_D8s_v5": 1, "Standard_D16s_v5": 1},
        image_id="Canonical:0001-com-ubuntu-server-jammy:22_04-lts:latest",
        ssh_public_keys=["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABg azure@test"],
        max_instances=2,
    )

    assert template.vm_sizes == ["Standard_D8s_v5", "Standard_D16s_v5"]
