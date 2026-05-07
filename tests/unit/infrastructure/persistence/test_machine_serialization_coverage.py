"""Drift validator: ensures MachineSerializer.to_dict covers all Machine fields.

If a field is added to Machine but not to MachineSerializer (and not explicitly
excluded), this test will fail immediately — catching the omission before it
reaches production and causes silent data loss.
"""

import pytest

from orb.domain.base.value_objects import InstanceType
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.machine_status import MachineStatus


def _make_minimal_machine() -> Machine:
    """Build the smallest valid Machine instance sufficient for serialization."""
    return Machine(
        machine_id=MachineId(value="i-1234567890abcdef0"),
        template_id="template-001",
        request_id="request-001",
        provider_type="aws",
        provider_name="aws-us-east-1",
        instance_type=InstanceType(value="t2.micro"),
        image_id="ami-12345678",
        status=MachineStatus.PENDING,
    )


@pytest.mark.unit
def test_machine_serializer_covers_all_non_excluded_fields():
    """If a field is added to Machine, MachineSerializer.to_dict must cover it.

    Failure modes:
    - ``missing``: field exists on Machine, is not excluded, but to_dict omits it
      → add the field to MachineSerializer.to_dict, or add it to
        Machine._SERIALIZATION_EXCLUDED_FIELDS with a comment explaining why.
    - ``extra``: to_dict emits a key that is not a Machine field and is not a
      known serializer-only meta key (e.g. schema_version)
      → remove the stale key from MachineSerializer.to_dict.
    """
    from orb.infrastructure.storage.repositories.machine_repository import MachineSerializer

    machine = _make_minimal_machine()
    serializer = MachineSerializer()
    serialized_keys = set(serializer.to_dict(machine).keys())
    model_fields = set(Machine.model_fields.keys())
    excluded = Machine._SERIALIZATION_EXCLUDED_FIELDS

    # Keys emitted by the serializer that have no corresponding model field.
    # schema_version is a migration-support meta key intentionally added by
    # the serializer and has no place on the domain model.
    serializer_only_keys = {"schema_version"}

    missing = (model_fields - excluded) - serialized_keys
    extra = (serialized_keys - serializer_only_keys) - model_fields

    assert not missing, (
        f"MachineSerializer.to_dict is missing fields from Machine: {missing}\n"
        "Either add them to MachineSerializer.to_dict or to "
        "Machine._SERIALIZATION_EXCLUDED_FIELDS with a comment."
    )
    assert not extra, (
        f"MachineSerializer.to_dict emits keys not present on Machine: {extra}\n"
        "Either add them to Machine as fields or remove them from to_dict."
    )
