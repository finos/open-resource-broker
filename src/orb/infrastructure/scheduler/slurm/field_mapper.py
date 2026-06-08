"""SLURM-specific field mapping and transformations."""

from typing import Any, Dict, List

from orb.infrastructure.scheduler.base.field_mapper import SchedulerFieldMapper
from orb.infrastructure.scheduler.slurm.field_mappings import (
    ORB_TEMPLATE_TO_SLURM_PARTITION,
    SLURM_NODE_STATE_TO_ORB_MACHINE_STATUS,
    SLURM_PARTITION_TO_ORB_TEMPLATE,
)


class SlurmFieldMapper(SchedulerFieldMapper):
    """SLURM-specific field mapping between partition/node format and ORB domain."""

    @property
    def field_mappings(self) -> Dict[str, str]:
        """SLURM partition → ORB template field mappings."""
        return SLURM_PARTITION_TO_ORB_TEMPLATE

    def map_input_fields(self, external_template: Dict[str, Any]) -> Dict[str, Any]:
        """Map SLURM partition/node format → ORB domain format."""
        mapped: Dict[str, Any] = {}

        for slurm_field, orb_field in self.field_mappings.items():
            if slurm_field in external_template:
                value = external_template[slurm_field]
                # Transform state string to boolean is_active
                if slurm_field == "state" and orb_field == "is_active":
                    mapped[orb_field] = (
                        value.upper() == "UP" if isinstance(value, str) else bool(value)
                    )
                else:
                    mapped[orb_field] = value

        # Copy unmapped fields
        for key, value in external_template.items():
            if key not in self.field_mappings and key not in mapped:
                mapped[key] = value

        return mapped

    def map_output_fields(
        self, internal_template: Dict[str, Any], copy_unmapped: bool = True
    ) -> Dict[str, Any]:
        """Map ORB domain format → SLURM partition format."""
        mapped: Dict[str, Any] = {}

        for orb_field, slurm_field in ORB_TEMPLATE_TO_SLURM_PARTITION.items():
            if orb_field in internal_template:
                value = internal_template[orb_field]
                # Transform boolean is_active back to state string
                if orb_field == "is_active" and slurm_field == "state":
                    mapped[slurm_field] = "UP" if value else "DOWN"
                else:
                    mapped[slurm_field] = value

        if copy_unmapped:
            for key, value in internal_template.items():
                if key not in ORB_TEMPLATE_TO_SLURM_PARTITION and key not in mapped:
                    mapped[key] = value

        return mapped

    def format_for_generation(
        self, internal_templates: List[Dict[str, Any]], copy_unmapped: bool = False
    ) -> List[Dict[str, Any]]:
        """Format internal templates for SLURM's expected format."""
        return [self.map_output_fields(t, copy_unmapped=copy_unmapped) for t in internal_templates]

    @staticmethod
    def map_node_state(slurm_state: str) -> str:
        """Map a SLURM node state string to an ORB machine status.

        Handles compound states (e.g. "IDLE+DRAIN") by splitting on "+"
        and checking flags. The base state is used for the primary lookup;
        if a flag like DRAIN is present, it overrides to "failed".
        """
        if not slurm_state:
            return "failed"

        parts = slurm_state.upper().split("+")
        base_state = parts[0]

        # Flags that force a failed status regardless of base state
        failure_flags = {"DRAIN", "FAIL", "NOT_RESPONDING"}
        if any(flag in parts[1:] for flag in failure_flags):
            return "failed"

        return SLURM_NODE_STATE_TO_ORB_MACHINE_STATUS.get(base_state, "failed")
