"""Unit tests for domain operation value objects."""

import pytest

from orb.domain.base.operations import Operation, OperationResult, OperationType


@pytest.mark.unit
class TestOperationType:
    def test_all_expected_members_present(self):
        names = {e.name for e in OperationType}
        assert "CREATE_INSTANCES" in names
        assert "TERMINATE_INSTANCES" in names
        assert "HEALTH_CHECK" in names

    def test_string_value_round_trip(self):
        assert OperationType("create_instances") == OperationType.CREATE_INSTANCES

    def test_is_str_enum(self):
        assert isinstance(OperationType.HEALTH_CHECK, str)


@pytest.mark.unit
class TestOperation:
    def test_creates_with_valid_params(self):
        op = Operation(operation_type=OperationType.CREATE_INSTANCES, parameters={"count": 5})
        assert op.operation_type == OperationType.CREATE_INSTANCES
        assert op.parameters == {"count": 5}
        assert op.context is None

    def test_creates_with_context(self):
        op = Operation(
            operation_type=OperationType.HEALTH_CHECK,
            parameters={},
            context={"region": "us-east-1"},
        )
        assert op.context == {"region": "us-east-1"}

    def test_raises_when_parameters_not_dict(self):
        with pytest.raises(ValueError, match="parameters must be a dictionary"):
            Operation(operation_type=OperationType.CREATE_INSTANCES, parameters="bad")  # type: ignore[arg-type]

    def test_raises_when_context_not_dict_or_none(self):
        with pytest.raises(ValueError, match="context must be a dictionary or None"):
            Operation(
                operation_type=OperationType.CREATE_INSTANCES,
                parameters={},
                context="bad",  # type: ignore[arg-type]
            )

    def test_none_context_is_accepted(self):
        op = Operation(
            operation_type=OperationType.VALIDATE_TEMPLATE,
            parameters={},
            context=None,
        )
        assert op.context is None

    def test_empty_parameters_dict_accepted(self):
        op = Operation(operation_type=OperationType.GET_AVAILABLE_TEMPLATES, parameters={})
        assert op.parameters == {}


@pytest.mark.unit
class TestOperationResult:
    def test_success_result_factory(self):
        result = OperationResult.success_result(data={"id": "i-123"})
        assert result.success is True
        assert result.data == {"id": "i-123"}
        assert result.error_message is None
        assert result.metadata == {}

    def test_success_result_with_metadata(self):
        result = OperationResult.success_result(data=None, metadata={"region": "eu-west-1"})
        assert result.metadata == {"region": "eu-west-1"}

    def test_error_result_factory(self):
        result = OperationResult.error_result("something went wrong", error_code="E001")
        assert result.success is False
        assert result.error_message == "something went wrong"
        assert result.error_code == "E001"
        assert result.data is None

    def test_error_result_without_code(self):
        result = OperationResult.error_result("oops")
        assert result.error_code is None

    def test_metadata_defaults_to_empty_dict_when_none(self):
        result = OperationResult(success=True, data=None, metadata=None)  # type: ignore[arg-type]
        assert result.metadata == {}

    def test_direct_construction_success(self):
        result = OperationResult(success=True, data=42)
        assert result.success is True
        assert result.data == 42
        assert result.metadata == {}
