"""Protocol defining per-provider field-mapping behaviour for the HostFactory scheduler."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class FieldMappingPort(Protocol):
    """Per-provider HostFactory field-mapping extension point.

    Implementations live under ``providers/<name>/scheduler/`` and are
    registered via ``FieldMappingRegistry`` during provider bootstrap.
    """

    def get_mappings(self) -> dict[str, str]:
        """Return the provider-specific HF-field → internal-field name dict.

        The shared ``HostFactoryFieldMappings.MAPPINGS["generic"]`` table is
        always applied first; this method returns only the *additional*
        provider-specific entries that should be merged on top.
        """
        ...  # type: ignore[return]

    def apply_defaults(self, mapped: dict) -> dict:
        """Apply provider-specific ``setdefault`` logic after field mapping.

        Args:
            mapped: The partially-mapped template dict (mutated in-place and
                    returned for convenience).

        Returns:
            The same dict with provider defaults applied.
        """
        ...  # type: ignore[return]

    def derive_attributes(self, machine_type: str | None) -> dict[str, list[str]] | None:
        """Build the HF ``attributes`` object for a given machine / instance type.

        Args:
            machine_type: Provider-specific instance/machine type string
                          (e.g. ``"t3.medium"`` for AWS EC2).

        Returns:
            A dict suitable for the HF ``attributes`` field, or ``None`` when
            the provider does not support cpu/ram attribute derivation.
        """
        ...  # type: ignore[return]
