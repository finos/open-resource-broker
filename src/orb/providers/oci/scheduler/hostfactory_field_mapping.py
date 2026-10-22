"""OCI-specific HostFactory aliases."""


class OCIFieldMapping:
    def get_mappings(self) -> dict[str, str]:
        mappings = {
            "subnetId": "subnet_ids",
            "subnetIds": "subnet_ids",
            "securityGroupIds": "security_group_ids",
            "userDataScript": "machine_bootstrap",
            "compartmentId": "compartment_id",
            "availabilityDomain": "availability_domain",
            "computeClusterId": "compute_cluster_id",
            "agentConfig": "agent_config",
            "shape": "shape",
            "shapeConfig": "shape_config",
            "ocpus": "ocpus",
            "memoryInGBs": "memory_gbs",
            "bootVolumeSizeInGBs": "boot_volume_gbs",
            "capacityType": "capacity_type",
            "fallbackToOndemand": "fallback_to_ondemand",
            "preemptiblePreserveBootVolume": "preemptible_preserve_boot_volume",
            "sshAuthorizedKeys": "ssh_authorized_keys",
            "formFactor": "form_factor",
            "cpuVendor": "cpu_vendor",
            "generation": "generation",
        }
        generic = {"subnetId", "subnetIds", "securityGroupIds", "userDataScript"}
        return {
            key: value if key in generic else f"provider_config.{value}"
            for key, value in mappings.items()
        }

    def apply_defaults(self, mapped: dict) -> dict:
        mapped.setdefault("max_machines", 1)
        return mapped

    def derive_attributes(self, machine_type: str | None) -> dict[str, list[str]] | None:
        # Flex shapes do not determine CPU/memory without shape_config.
        return None
