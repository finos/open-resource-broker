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
        # First apply base mapping with nested field support
        mapped = self._map_with_nested_support(external_template, self.field_mappings)

        # Apply HostFactory-specific input transformations
        return self._apply_input_transformations(mapped)

    def map_output_fields(
        self, internal_template: Dict[str, Any], copy_unmapped: bool = False
    ) -> Dict[str, Any]:
        """Map internal format → HostFactory format with transformations."""
        # Apply internal → external mappings with nested field support
        reverse_mappings = {v: k for k, v in self.field_mappings.items()}
        mapped = self._map_with_nested_support(
            internal_template, reverse_mappings, reverse=True, copy_unmapped=copy_unmapped
        )

        # Apply HostFactory-specific transformations
        return self._apply_output_transformations(mapped)

    def _map_with_nested_support(
        self,
        source: Dict[str, Any],
        mappings: Dict[str, str],
        reverse: bool = False,
        copy_unmapped: bool = True,
    ) -> Dict[str, Any]:
        """Map fields with support for nested provider_data access."""
        mapped = {}

        for source_field, target_field in mappings.items():
            if reverse:
                # For output mapping: internal → external
                if "." in source_field:
                    # Handle nested field access (e.g., provider_data.fleet_type)
                    value = self._get_nested_value(source, source_field)
                    if value is not None:
                        mapped[target_field] = value
                elif source_field in source:
                    mapped[target_field] = source[source_field]
            # For input mapping: external → internal
            elif source_field in source:
                if "." in target_field:
                    # Handle nested field setting (e.g., provider_data.fleet_type)
                    self._set_nested_value(mapped, target_field, source[source_field])
                else:
                    mapped[target_field] = source[source_field]

        # Only copy unmapped fields if requested
        if copy_unmapped:
            for key, value in source.items():
                if not reverse and key not in mappings and key not in mapped:
                    mapped[key] = value
                elif reverse and key not in {v for v in mappings.keys() if "." not in v}:
                    # Only copy if not a nested source field
                    if key not in mapped:
                        mapped[key] = value

        return mapped

    def _get_nested_value(self, data: Dict[str, Any], nested_key: str) -> Any:
        """Get value from nested dictionary using dot notation."""
        keys = nested_key.split(".")
        value = data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None
        return value

    def _set_nested_value(self, data: Dict[str, Any], nested_key: str, value: Any) -> None:
        """Set value in nested dictionary using dot notation."""
        keys = nested_key.split(".")
        current = data

        # Navigate to the parent of the target key
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        # Set the final value
        current[keys[-1]] = value

    def _apply_input_transformations(self, mapped: Dict[str, Any]) -> Dict[str, Any]:
        """Apply HostFactory-specific input transformations."""
        from infrastructure.scheduler.hostfactory.transformations import HostFactoryTransformations

        # Apply all transformations from the transformations module
        return HostFactoryTransformations.apply_transformations(mapped)

    def _apply_output_transformations(self, mapped: Dict[str, Any]) -> Dict[str, Any]:
        """Apply HostFactory-specific transformations."""
        # Transform machine_types to vmType/vmTypes for HF output
        if "machine_types" in mapped:
            machine_types = mapped["machine_types"]
            if machine_types:
                if len(machine_types) == 1 and next(iter(machine_types.values())) == 1:
                    # Single type with weight 1 → vmType
                    mapped["vmType"] = next(iter(machine_types.keys()))
                else:
                    # Multiple types or custom weights → vmTypes
                    mapped["vmTypes"] = machine_types
            # Remove internal field from output
            del mapped["machine_types"]

        # Transform vmType → HF attributes (vmType is the external field name)
        if "vmType" in mapped:
            mapped["attributes"] = self._create_hf_attributes(mapped["vmType"])

        # Convert subnetIds to subnetId (always comma-joined so validate_subnet_id
        # split(",") covers all configured subnets regardless of list length)
        if mapped.get("subnetIds"):
            mapped["subnetId"] = ",".join(mapped["subnetIds"])
            del mapped["subnetIds"]

        # Strip None, empty dicts, and empty lists from output
        return {k: v for k, v in mapped.items() if v is not None and v != {} and v != []}

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
