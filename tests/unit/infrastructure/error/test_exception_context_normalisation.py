"""Built-in exception wrappers accept an ExceptionContext and keep details serialisable."""

import json
from http import HTTPStatus

import pytest

from orb.domain.base.exceptions import ConfigurationError, InfrastructureError
from orb.infrastructure.error.categories import ErrorCode
from orb.infrastructure.error.context import ExceptionContext
from orb.infrastructure.error.decorators import handle_exceptions
from orb.infrastructure.error.exception_handler import ExceptionHandler


def _json_decode_error() -> json.JSONDecodeError:
    try:
        json.loads("{not json")
    except json.JSONDecodeError as exc:
        return exc
    raise AssertionError("expected a JSONDecodeError")


WRAPPED_EXCEPTIONS = [
    pytest.param(_json_decode_error(), id="json_decode_error"),
    pytest.param(ConnectionError("refused"), id="connection_error"),
    pytest.param(FileNotFoundError(2, "No such file", "/tmp/templates.json"), id="file_not_found"),
    pytest.param(ValueError("bad value"), id="value_error"),
    pytest.param(KeyError("missing"), id="key_error"),
    pytest.param(TypeError("wrong type"), id="type_error"),
    pytest.param(AttributeError("no attr"), id="attribute_error"),
    pytest.param(RuntimeError("boom"), id="generic_exception"),
]


@pytest.mark.unit
class TestExceptionContextNormalisation:
    @pytest.mark.parametrize("exc", WRAPPED_EXCEPTIONS)
    def test_wrapper_stores_operation_name_and_details_serialise(self, exc: Exception) -> None:
        handler = ExceptionHandler()

        result = handler.handle(exc, ExceptionContext(operation="load_template"))

        details = result.details  # type: ignore[attr-defined]
        assert details["context"] == "load_template"
        json.dumps(details)

    @pytest.mark.parametrize(
        ("exc", "fallback"),
        [
            pytest.param(ConnectionError("x"), "network_operation", id="connection_error"),
            pytest.param(ValueError("x"), "value_validation", id="value_error"),
            pytest.param(KeyError("x"), "key_access", id="key_error"),
            pytest.param(TypeError("x"), "type_validation", id="type_error"),
            pytest.param(AttributeError("x"), "attribute_access", id="attribute_error"),
            pytest.param(FileNotFoundError("x"), "file_operation", id="file_not_found"),
        ],
    )
    def test_wrapper_keeps_its_fallback_when_operation_is_empty(
        self, exc: Exception, fallback: str
    ) -> None:
        handler = ExceptionHandler()

        result = handler.handle(exc, ExceptionContext(operation=""))

        assert result.details["context"] == fallback  # type: ignore[attr-defined]

    def test_plain_string_context_still_accepted(self) -> None:
        handler = ExceptionHandler()

        result = handler._wrap_value_error(ValueError("x"), "parse_request")

        assert result.details["context"] == "parse_request"

    def test_file_not_found_under_template_operation_maps_to_configuration_error(self) -> None:
        handler = ExceptionHandler()

        @handle_exceptions(context="load_template", layer="infrastructure", handler=handler)
        def load() -> None:
            raise FileNotFoundError(2, "No such file", "/etc/orb/templates.json")

        with pytest.raises(ConfigurationError) as exc_info:
            load()

        assert exc_info.value.details["context"] == "load_template"
        assert exc_info.value.details["filename"] == "/etc/orb/templates.json"

        response = handler.handle_error_for_http(exc_info.value)
        assert response.error_code == ErrorCode.INTERNAL_ERROR
        assert response.message == "A configuration error occurred"
        assert response.http_status == HTTPStatus.INTERNAL_SERVER_ERROR

    def test_file_not_found_outside_config_operation_maps_to_infrastructure_error(self) -> None:
        handler = ExceptionHandler()

        @handle_exceptions(context="read_machine_state", layer="infrastructure", handler=handler)
        def read() -> None:
            raise FileNotFoundError(2, "No such file", "/var/lib/orb/state")

        with pytest.raises(InfrastructureError) as exc_info:
            read()

        assert not isinstance(exc_info.value, ConfigurationError)
        assert exc_info.value.details["context"] == "read_machine_state"
