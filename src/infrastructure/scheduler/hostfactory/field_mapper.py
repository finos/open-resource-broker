"""HostFactory-specific field mapping and transformations."""

from typing import Any, Dict, List

from infrastructure.scheduler.base.field_mapper import SchedulerFieldMapper
from infrastructure.scheduler.hostfactory.field_mappings import HostFactoryFieldMappings


class HostFactoryFieldMapper(SchedulerFieldMapper):
    """HostFactory-specific field mapping and transformations."""

    def __init__(self, provider_type: str = "aws"):
        self.provider_type = provider_type

    @property
    def field_mappings(self) -> Dict[str, str]:
        """Get HostFactory field mappings for the provider."""
        return HostFactoryFieldMappings.get_mappings(self.provider_type)

    def map_input_fields(self, external_template: Dict[str, Any]) -> Dict[str, Any]:
        """Map HostFactory format → internal format with transformations."""
        # First apply base mapping
        mapped = super().map_input_fields(external_template)
        
        # Apply HostFactory-specific input transformations
        return self._apply_input_transformations(mapped)

    def map_output_fields(self, internal_template: Dict[str, Any]) -> Dict[str, Any]:
        """Map internal format → HostFactory format with transformations."""
        # Apply internal → external mappings (only mapped fields)
        reverse_mappings = {v: k for k, v in self.field_mappings.items()}
        mapped = {}

        for internal_field, external_field in reverse_mappings.items():
            if internal_field in internal_template:
                mapped[external_field] = internal_template[internal_field]

        # Copy unmapped fields (preserve fields not in mappings)
        for key, value in internal_template.items():
            if key not in reverse_mappings:
                mapped[key] = value

        # Apply HostFactory-specific transformations
        return self._apply_output_transformations(mapped)

    def _apply_input_transformations(self, mapped: Dict[str, Any]) -> Dict[str, Any]:
        """Apply HostFactory-specific input transformations."""
        from infrastructure.scheduler.hostfactory.transformations import HostFactoryTransformations
        
        # Apply all transformations from the transformations module
        return HostFactoryTransformations.apply_transformations(mapped)

    def _apply_output_transformations(self, mapped: Dict[str, Any]) -> Dict[str, Any]:
        """Apply HostFactory-specific transformations."""
        # Transform vmType → HF attributes (vmType is the external field name)
        if "vmType" in mapped:
            mapped["attributes"] = self._create_hf_attributes(mapped["vmType"])

        # Convert subnetIds to subnetId (comma-separated string for HF)
        if mapped.get("subnetIds"):
            if isinstance(mapped["subnetIds"], list) and len(mapped["subnetIds"]) > 1:
                mapped["subnetId"] = ",".join(mapped["subnetIds"])
            else:
                mapped["subnetId"] = mapped["subnetIds"][0] if mapped["subnetIds"] else ""
            del mapped["subnetIds"]

        return mapped

    def _create_hf_attributes(self, instance_type: str) -> Dict[str, List[str]]:
        """Create HostFactory attributes from instance type."""
        from cli.field_mapping import derive_cpu_ram_from_instance_type

        ncpus, nram = derive_cpu_ram_from_instance_type(instance_type)

        return {
            "type": ["String", "X86_64"],
            "ncpus": ["Numeric", ncpus],
            "ncores": ["Numeric", ncpus],
            "nram": ["Numeric", nram],
        }
