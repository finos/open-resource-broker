"""Unit tests for domain filter service and generic filter service."""

import pytest

from domain.services.filter_service import FilterOperator, MachineFilter
from domain.services.generic_filter_service import GenericFilter, GenericFilterService


class TestFilterOperator:
    def test_exact_match(self):
        assert FilterOperator.EXACT.apply("running", "running") is True
        assert FilterOperator.EXACT.apply("running", "stopped") is False

    def test_exact_match_case_insensitive(self):
        assert FilterOperator.EXACT.apply("Running", "running") is True

    def test_exact_match_wildcard(self):
        assert FilterOperator.EXACT.apply("i-abc123", "i-*") is True
        assert FilterOperator.EXACT.apply("x-abc123", "i-*") is False

    def test_contains(self):
        assert FilterOperator.CONTAINS.apply("i-1234567890abcdef0", "1234") is True
        assert FilterOperator.CONTAINS.apply("i-1234567890abcdef0", "xyz") is False

    def test_contains_case_insensitive(self):
        assert FilterOperator.CONTAINS.apply("Running", "run") is True

    def test_regex_match(self):
        assert FilterOperator.REGEX.apply("i-abc123", r"i-[a-z0-9]+") is True
        assert FilterOperator.REGEX.apply("x-abc123", r"^i-") is False

    def test_regex_invalid_pattern_returns_false(self):
        assert FilterOperator.REGEX.apply("value", "[invalid") is False

    def test_not_regex(self):
        assert FilterOperator.NOT_REGEX.apply("x-abc123", r"^i-") is True
        assert FilterOperator.NOT_REGEX.apply("i-abc123", r"^i-") is False

    def test_not_equal(self):
        assert FilterOperator.NOT_EQUAL.apply("running", "stopped") is True
        assert FilterOperator.NOT_EQUAL.apply("running", "running") is False

    def test_not_equal_wildcard(self):
        assert FilterOperator.NOT_EQUAL.apply("x-abc", "i-*") is True
        assert FilterOperator.NOT_EQUAL.apply("i-abc", "i-*") is False

    def test_none_field_value_returns_false(self):
        for op in FilterOperator:
            assert op.apply(None, "value") is False


class _FakeMachine:
    """Minimal fake machine for filter tests."""

    def __init__(self, status="running", instance_type="t3.medium", region="us-east-1"):
        self.status = status
        self.instance_type = instance_type
        self.region = region


class _FakeMachineWithMeta:
    """Fake machine with nested metadata attribute."""

    def __init__(self, status="running"):
        self.status = status
        self.metadata = _Meta()


class _Meta:
    env = "prod"


class TestMachineFilter:
    def test_matches_exact(self):
        f = MachineFilter(field="status", operator=FilterOperator.EXACT, value="running")
        assert f.matches(_FakeMachine(status="running")) is True
        assert f.matches(_FakeMachine(status="stopped")) is False

    def test_matches_contains(self):
        f = MachineFilter(field="instance_type", operator=FilterOperator.CONTAINS, value="t3")
        assert f.matches(_FakeMachine(instance_type="t3.medium")) is True
        assert f.matches(_FakeMachine(instance_type="m5.large")) is False

    def test_matches_missing_field_returns_false(self):
        f = MachineFilter(field="nonexistent_field", operator=FilterOperator.EXACT, value="x")
        assert f.matches(_FakeMachine()) is False

    def test_matches_nested_field(self):
        f = MachineFilter(field="metadata.env", operator=FilterOperator.EXACT, value="prod")
        assert f.matches(_FakeMachineWithMeta()) is True

    def test_matches_nested_field_missing(self):
        f = MachineFilter(field="metadata.missing", operator=FilterOperator.EXACT, value="x")
        assert f.matches(_FakeMachineWithMeta()) is False


class TestGenericFilter:
    def test_matches_dict_exact(self):
        f = GenericFilter(field="status", operator=FilterOperator.EXACT, value="running")
        assert f.matches({"status": "running"}) is True
        assert f.matches({"status": "stopped"}) is False

    def test_matches_nested_dict(self):
        f = GenericFilter(field="meta.env", operator=FilterOperator.EXACT, value="prod")
        assert f.matches({"meta": {"env": "prod"}}) is True
        assert f.matches({"meta": {"env": "dev"}}) is False

    def test_matches_missing_key_returns_false(self):
        f = GenericFilter(field="missing", operator=FilterOperator.EXACT, value="x")
        assert f.matches({"status": "running"}) is False


class TestGenericFilterService:
    def setup_method(self):
        self.svc = GenericFilterService()

    def test_parse_exact_filter(self):
        filters = self.svc.parse_filters(["status=running"])
        assert len(filters) == 1
        assert filters[0].field == "status"
        assert filters[0].operator == FilterOperator.EXACT
        assert filters[0].value == "running"

    def test_parse_contains_filter(self):
        filters = self.svc.parse_filters(["name~web"])
        assert filters[0].operator == FilterOperator.CONTAINS
        assert filters[0].value == "web"

    def test_parse_regex_filter(self):
        filters = self.svc.parse_filters([r"id=~^i-[a-z0-9]+"])
        assert filters[0].operator == FilterOperator.REGEX

    def test_parse_not_regex_filter(self):
        filters = self.svc.parse_filters([r"id!~^x-"])
        assert filters[0].operator == FilterOperator.NOT_REGEX

    def test_parse_not_equal_filter(self):
        filters = self.svc.parse_filters(["status!=stopped"])
        assert filters[0].operator == FilterOperator.NOT_EQUAL
        assert filters[0].value == "stopped"

    def test_parse_invalid_filter_raises(self):
        with pytest.raises(ValueError, match="Invalid filter"):
            self.svc.parse_filters(["no-operator-here"])

    def test_parse_invalid_regex_raises(self):
        with pytest.raises(ValueError):
            self.svc.parse_filters(["id=~[invalid"])

    def test_apply_filters_empty_returns_all(self):
        objects = [{"status": "running"}, {"status": "stopped"}]
        result = self.svc.apply_filters(objects, [])
        assert result == objects

    def test_apply_filters_exact(self):
        objects = [
            {"status": "running", "type": "t3.medium"},
            {"status": "stopped", "type": "m5.large"},
        ]
        result = self.svc.apply_filters(objects, ["status=running"])
        assert len(result) == 1
        assert result[0]["status"] == "running"

    def test_apply_filters_multiple_and_logic(self):
        objects = [
            {"status": "running", "region": "us-east-1"},
            {"status": "running", "region": "eu-west-1"},
            {"status": "stopped", "region": "us-east-1"},
        ]
        result = self.svc.apply_filters(objects, ["status=running", "region=us-east-1"])
        assert len(result) == 1
        assert result[0]["region"] == "us-east-1"

    def test_apply_filters_no_matches(self):
        objects = [{"status": "running"}, {"status": "pending"}]
        result = self.svc.apply_filters(objects, ["status=stopped"])
        assert result == []

    def test_apply_filters_contains(self):
        objects = [
            {"name": "web-server-01"},
            {"name": "db-server-01"},
            {"name": "web-proxy-01"},
        ]
        result = self.svc.apply_filters(objects, ["name~web"])
        assert len(result) == 2

    def test_apply_filters_not_equal(self):
        objects = [
            {"status": "running"},
            {"status": "stopped"},
            {"status": "pending"},
        ]
        result = self.svc.apply_filters(objects, ["status!=stopped"])
        assert len(result) == 2
        statuses = {o["status"] for o in result}
        assert "stopped" not in statuses
