"""HostFactory-specific field mapping and transformations."""

from typing import Any, Dict, List

from orb.infrastructure.scheduler.base.field_mapper import SchedulerFieldMapper
from orb.infrastructure.scheduler.hostfactory.field_mappings import HostFactoryFieldMappings


class HostFactoryFieldMapper(SchedulerFieldMapper):
    """HostFactory-specific field mapping and transformations."""

    # Internal-name canonicalisation for the output direction.  The internal
    # (snake_case) vocabulary was renamed on the domain ``Template``, but the
    # application ``TemplateDTO`` deliberately keeps the original field names as
    # a translation seam.  Output can therefore arrive keyed by either
    # vocabulary depending on whether it originated from ``Template.model_dump``
    # (new names) or ``TemplateDTO.to_dict`` (original names).  This table folds
    # any original names onto the canonical internal name before the reverse
    # field mapping runs, so both sources produce identical HostFactory output.
    _OUTPUT_CANONICAL_NAMES: Dict[str, str] = {
        "image_id": "machine_image",
        "max_instances": "max_machines",
        "key_name": "machine_ssh_key",
        "user_data": "machine_bootstrap",
        "root_device_volume_size": "machine_disk_size_gb",
        "volume_type": "machine_disk_type",
        "instance_profile": "machine_role",
    }

    def __init__(self, provider_type: str | None = None):
        self.provider_type = provider_type

    @property
    def field_mappings(self) -> Dict[str, str]:
        """Get HostFactory field mappings for the provider.

        Merges the static ``HostFactoryFieldMappings`` table (generic +
        the no-bootstrap AWS fallback) with the per-provider adapter
        registered via :class:`FieldMappingRegistry` during provider
        bootstrap.  Adapter entries win on conflicting keys, mirroring
        the documented integration order in :class:`FieldMappingPort`.
        """
        from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import (
            FieldMappingRegistry,
        )

        base = dict(HostFactoryFieldMappings.get_mappings(self.provider_type or ""))
        adapter = FieldMappingRegistry.get_or_none(self.provider_type or "")
        if adapter is not None:
            base.update(adapter.get_mappings())
        return base

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
        # Fold any original (pre-rename) internal names onto the canonical name
        # so DTO-sourced dicts (original names) and domain-sourced dicts (renamed
        # names) both map through the reverse field-mapping identically.
        internal_template = self._canonicalize_internal_names(internal_template)

        # Apply internal → external mappings with nested field support
        reverse_mappings = {v: k for k, v in self.field_mappings.items()}
        mapped = self._map_with_nested_support(
            internal_template, reverse_mappings, reverse=True, copy_unmapped=copy_unmapped
        )

        # Apply HostFactory-specific transformations
        return self._apply_output_transformations(mapped)

    def _canonicalize_internal_names(self, source: Dict[str, Any]) -> Dict[str, Any]:
        """Fold original internal field names onto their renamed canonical form.

        Only rewrites a key when the canonical name is not already present, so a
        dict that already uses the new names is returned unchanged.
        """
        if not any(old in source for old in self._OUTPUT_CANONICAL_NAMES):
            return source
        out = dict(source)
        for old_name, new_name in self._OUTPUT_CANONICAL_NAMES.items():
            if old_name in out and new_name not in out:
                out[new_name] = out.pop(old_name)
        return out

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
        from orb.infrastructure.scheduler.hostfactory.transformations import (
            HostFactoryTransformations,
        )

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
        """Create HostFactory attributes from instance type via the per-provider registry.

        Delegates to the registered ``FieldMappingPort.derive_attributes`` for
        the active provider type.  Falls back to a minimal placeholder when no
        adapter is registered (non-AWS providers that don't supply cpu/ram
        lookup yet).
        """
        from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import (
            FieldMappingRegistry,
        )

        adapter = FieldMappingRegistry.get_or_none(self.provider_type or "")
        if adapter is not None:
            result = adapter.derive_attributes(instance_type)
            if result is not None:
                return result  # type: ignore[return-value]

        # Fallback: return a minimal attributes object so HF spec is still satisfied.
        return {
            "type": ["String", "X86_64"],
            "ncpus": ["Numeric", "1"],
            "nram": ["Numeric", "1024"],
        }
