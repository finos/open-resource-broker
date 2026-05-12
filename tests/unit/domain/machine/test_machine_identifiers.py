"""Unit tests for MachineId self-flattening serialization."""

from orb.domain.machine.machine_identifiers import MachineId


def test_machine_id_self_flattens_to_string():
    """MachineId(value='i-xxx').model_dump() returns 'i-xxx', not {'value': 'i-xxx'}."""
    mid = MachineId(value="i-abc")
    assert mid.model_dump() == "i-abc"


def test_machine_id_reconstructs_from_string():
    """MachineId.model_validate('i-xxx') returns MachineId(value='i-xxx')."""
    mid = MachineId.model_validate("i-abc")
    assert mid.value == "i-abc"


def test_machine_id_still_accepts_dict_form():
    """Backward-compat: MachineId.model_validate({'value': 'i-xxx'}) still works."""
    mid = MachineId.model_validate({"value": "i-abc"})
    assert mid.value == "i-abc"
