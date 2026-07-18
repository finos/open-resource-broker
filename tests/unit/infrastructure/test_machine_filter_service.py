"""Unit tests for services/machine_filter_service.py."""

import pytest

from orb.domain.services.filter_service import FilterOperator, MachineFilter
from orb.infrastructure.services.machine_filter_service import MachineFilterService


@pytest.mark.unit
class TestMachineFilterServiceParseFilters:
    """Tests for parse_filters — operator routing."""

    def setup_method(self) -> None:
        self.svc = MachineFilterService()

    def test_exact_match_operator(self) -> None:
        filters = self.svc.parse_filters(["status=running"])
        assert len(filters) == 1
        assert filters[0].operator == FilterOperator.EXACT
        assert filters[0].field == "status"
        assert filters[0].value == "running"

    def test_contains_operator(self) -> None:
        filters = self.svc.parse_filters(["name~prod"])
        assert filters[0].operator == FilterOperator.CONTAINS
        assert filters[0].field == "name"
        assert filters[0].value == "prod"

    def test_regex_operator(self) -> None:
        filters = self.svc.parse_filters(["hostname=~^web-"])
        assert filters[0].operator == FilterOperator.REGEX
        assert filters[0].field == "hostname"
        assert filters[0].value == "^web-"

    def test_not_regex_operator(self) -> None:
        filters = self.svc.parse_filters(["hostname!~^internal"])
        assert filters[0].operator == FilterOperator.NOT_REGEX
        assert filters[0].field == "hostname"
        assert filters[0].value == "^internal"

    def test_not_equal_operator(self) -> None:
        filters = self.svc.parse_filters(["status!=stopped"])
        assert filters[0].operator == FilterOperator.NOT_EQUAL
        assert filters[0].field == "status"
        assert filters[0].value == "stopped"

    def test_multiple_filters_parsed(self) -> None:
        filters = self.svc.parse_filters(["status=running", "region=us-east-1"])
        assert len(filters) == 2

    def test_empty_list_returns_empty(self) -> None:
        assert self.svc.parse_filters([]) == []

    def test_whitespace_trimmed_from_field_and_value(self) -> None:
        filters = self.svc.parse_filters(["  status = running  "])
        assert filters[0].field == "status"
        assert filters[0].value == "running"

    def test_invalid_expression_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid filter"):
            self.svc.parse_filters(["no_operator_here"])


@pytest.mark.unit
class TestMachineFilterServiceValidateRegex:
    """Tests for _validate_regex."""

    def setup_method(self) -> None:
        self.svc = MachineFilterService()

    def test_valid_regex_passes(self) -> None:
        # Should not raise
        self.svc._validate_regex("^web-.*$")

    def test_invalid_regex_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid regex"):
            self.svc._validate_regex("[invalid(")


@pytest.mark.unit
class TestMachineFilterServiceParseSingleFilter:
    """Tests for _parse_single_filter edge cases."""

    def setup_method(self) -> None:
        self.svc = MachineFilterService()

    def test_regex_operator_splits_on_first_occurrence(self) -> None:
        field, op, value = self.svc._parse_single_filter("name=~a=b")
        assert field == "name"
        assert op == FilterOperator.REGEX
        assert value == "a=b"

    def test_not_regex_operator(self) -> None:
        _field, op, _value = self.svc._parse_single_filter("tag!~prod")
        assert op == FilterOperator.NOT_REGEX

    def test_not_equal_detected_before_contains(self) -> None:
        # Ensure != takes priority over ~
        _field, op, _value = self.svc._parse_single_filter("status!=running")
        assert op == FilterOperator.NOT_EQUAL

    def test_invalid_expression_raises(self) -> None:
        with pytest.raises(ValueError):
            self.svc._parse_single_filter("justaplainstring")


@pytest.mark.unit
class TestMachineFilterReturnType:
    """Tests that parse_filters returns proper MachineFilter objects."""

    def test_returns_machine_filter_instances(self) -> None:
        svc = MachineFilterService()
        result = svc.parse_filters(["status=running"])
        assert all(isinstance(f, MachineFilter) for f in result)

    def test_machine_filter_is_frozen_dataclass(self) -> None:
        svc = MachineFilterService()
        f = svc.parse_filters(["status=running"])[0]
        with pytest.raises((AttributeError, TypeError)):
            f.field = "other"  # type: ignore[misc]
