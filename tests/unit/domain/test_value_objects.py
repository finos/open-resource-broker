"""Unit tests for Pydantic-based value objects (self-flattening serialization)."""

from orb.domain.base.value_objects import InstanceId, InstanceType, Tags


class TestInstanceTypeSelfFlatten:
    def test_instance_type_self_flattens(self):
        t = InstanceType(value="m5.large")
        assert t.model_dump() == "m5.large"

    def test_instance_type_accepts_string(self):
        t = InstanceType.model_validate("m5.large")
        assert t.value == "m5.large"

    def test_instance_type_accepts_dict(self):
        t = InstanceType.model_validate({"value": "m5.large"})
        assert t.value == "m5.large"


class TestInstanceIdSelfFlatten:
    def test_instance_id_self_flattens(self):
        i = InstanceId(value="i-abc123")
        assert i.model_dump() == "i-abc123"

    def test_instance_id_accepts_string(self):
        i = InstanceId.model_validate("i-abc123")
        assert i.value == "i-abc123"

    def test_instance_id_accepts_dict(self):
        i = InstanceId.model_validate({"value": "i-abc123"})
        assert i.value == "i-abc123"


class TestTagsSelfFlatten:
    def test_tags_self_flattens_to_dict(self):
        t = Tags(tags={"Env": "prod"})
        assert t.model_dump() == {"Env": "prod"}

    def test_tags_accepts_plain_dict(self):
        t = Tags.model_validate({"Env": "prod"})
        assert t.tags == {"Env": "prod"}

    def test_tags_accepts_nested_dict(self):
        t = Tags.model_validate({"tags": {"Env": "prod"}})
        assert t.tags == {"Env": "prod"}

    def test_tags_empty_self_flattens(self):
        t = Tags()
        assert t.model_dump() == {}
