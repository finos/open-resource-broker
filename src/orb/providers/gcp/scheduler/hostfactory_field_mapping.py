"""GCP field mapping for HostFactory templates."""

from __future__ import annotations


class GCPFieldMapping:
    """Map GCP HostFactory fields to the GCP template contract."""

    _MAPPINGS = {
        "vmType": "machine_type",
        "projectId": "project_id",
        "region": "region",
        "zones": "zones",
        "migScope": "mig_scope",
        "network": "network",
        "subnetwork": "subnetwork",
        "serviceAccountEmail": "service_account_email",
        "serviceAccountScopes": "service_account_scopes",
        "networkTags": "network_tags",
        "labels": "labels",
        "sourceImage": "source_image",
        "sourceImageFamily": "source_image_family",
        "sourceImageProject": "source_image_project",
        "bootDiskType": "boot_disk_type",
        "bootDiskSizeGb": "boot_disk_size_gb",
        "migName": "mig_name",
        "instanceTemplateNamePrefix": "instance_template_name_prefix",
    }

    def get_mappings(self) -> dict[str, str]:
        """Return GCP-specific HostFactory field mappings."""
        return dict(self._MAPPINGS)

    def apply_defaults(self, mapped: dict) -> dict:
        """Default a HostFactory template to one machine."""
        mapped.setdefault("max_machines", 1)
        return mapped

    def derive_attributes(self, machine_type: str | None) -> None:
        """GCP machine type names alone do not provide CPU and RAM attributes."""
        return None
