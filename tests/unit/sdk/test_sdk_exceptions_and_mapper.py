"""Unit tests for SDK exceptions and ParameterMapper.

Pure-logic tests — no network, no AWS, no external services.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# SDK exception hierarchy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSDKErrorBase:
    """SDKError base class stores message and details."""

    def _make(self, message="base error", details=None):
        from orb.sdk.exceptions import SDKError

        return SDKError(message, details)

    def test_message_stored(self):
        err = self._make("oops")
        assert err.message == "oops"

    def test_details_defaults_to_empty_dict(self):
        err = self._make("oops")
        assert err.details == {}

    def test_details_stored_when_provided(self):
        err = self._make("oops", {"key": "val"})
        assert err.details == {"key": "val"}

    def test_str_without_details_returns_message_only(self):
        err = self._make("plain message")
        assert str(err) == "plain message"

    def test_str_with_details_includes_details(self):
        err = self._make("base", {"code": 42})
        s = str(err)
        assert "base" in s
        assert "Details" in s
        assert "42" in s

    def test_is_exception(self):
        from orb.sdk.exceptions import SDKError

        assert issubclass(SDKError, Exception)


@pytest.mark.unit
class TestConfigurationError:
    """ConfigurationError carries config_key."""

    def _make(self, message="config error", config_key=None, details=None):
        from orb.sdk.exceptions import ConfigurationError

        return ConfigurationError(message, config_key=config_key, details=details)

    def test_config_key_stored(self):
        err = self._make(config_key="storage.strategy")
        assert err.config_key == "storage.strategy"

    def test_config_key_defaults_to_none(self):
        err = self._make()
        assert err.config_key is None

    def test_is_sdk_error(self):
        from orb.sdk.exceptions import ConfigurationError, SDKError

        assert issubclass(ConfigurationError, SDKError)

    def test_message_accessible(self):
        err = self._make("bad config")
        assert err.message == "bad config"


@pytest.mark.unit
class TestProviderError:
    """ProviderError carries provider attribute."""

    def _make(self, message="prov error", provider=None, details=None):
        from orb.sdk.exceptions import ProviderError

        return ProviderError(message, provider=provider, details=details)

    def test_provider_stored(self):
        err = self._make(provider="aws")
        assert err.provider == "aws"

    def test_provider_defaults_to_none(self):
        err = self._make()
        assert err.provider is None

    def test_is_sdk_error(self):
        from orb.sdk.exceptions import ProviderError, SDKError

        assert issubclass(ProviderError, SDKError)


@pytest.mark.unit
class TestHandlerDiscoveryError:
    """HandlerDiscoveryError carries handler_type."""

    def _make(self, message="discovery error", handler_type=None, details=None):
        from orb.sdk.exceptions import HandlerDiscoveryError

        return HandlerDiscoveryError(message, handler_type=handler_type, details=details)

    def test_handler_type_stored(self):
        err = self._make(handler_type="CommandHandler")
        assert err.handler_type == "CommandHandler"

    def test_handler_type_defaults_to_none(self):
        err = self._make()
        assert err.handler_type is None


@pytest.mark.unit
class TestMethodExecutionError:
    """MethodExecutionError carries method_name."""

    def _make(self, message="exec error", method_name=None, details=None):
        from orb.sdk.exceptions import MethodExecutionError

        return MethodExecutionError(message, method_name=method_name, details=details)

    def test_method_name_stored(self):
        err = self._make(method_name="create_request")
        assert err.method_name == "create_request"

    def test_method_name_defaults_to_none(self):
        err = self._make()
        assert err.method_name is None


@pytest.mark.unit
class TestNotFoundError:
    """NotFoundError formats message from entity_type and entity_id."""

    def _make(self, entity_type="Machine", entity_id="m-123"):
        from orb.sdk.exceptions import NotFoundError

        return NotFoundError(entity_type, entity_id)

    def test_message_contains_entity_type_and_id(self):
        err = self._make("Request", "req-abc")
        assert "Request" in str(err)
        assert "req-abc" in str(err)

    def test_entity_type_stored(self):
        err = self._make("Template", "t-1")
        assert err.entity_type == "Template"

    def test_entity_id_stored(self):
        err = self._make("Template", "t-1")
        assert err.entity_id == "t-1"

    def test_is_sdk_error(self):
        from orb.sdk.exceptions import NotFoundError, SDKError

        assert issubclass(NotFoundError, SDKError)


@pytest.mark.unit
class TestAlreadyExistsError:
    """AlreadyExistsError formats message from entity_type and entity_id."""

    def _make(self, entity_type="Machine", entity_id="m-dupe"):
        from orb.sdk.exceptions import AlreadyExistsError

        return AlreadyExistsError(entity_type, entity_id)

    def test_message_contains_entity_type_and_id(self):
        err = self._make("Request", "req-dupe")
        s = str(err)
        assert "Request" in s
        assert "req-dupe" in s

    def test_entity_type_and_id_stored(self):
        err = self._make("Machine", "m-1")
        assert err.entity_type == "Machine"
        assert err.entity_id == "m-1"


@pytest.mark.unit
class TestRequestTimeoutError:
    """RequestTimeoutError formats message and stores request_id / timeout."""

    def _make(self, request_id="req-abc", timeout=30.0):
        from orb.sdk.exceptions import RequestTimeoutError

        return RequestTimeoutError(request_id, timeout)

    def test_message_contains_request_id(self):
        err = self._make("req-xyz", 60.0)
        assert "req-xyz" in str(err)

    def test_message_contains_timeout(self):
        err = self._make("req-xyz", 60.0)
        assert "60" in str(err)

    def test_request_id_stored(self):
        err = self._make("req-123", 10.0)
        assert err.request_id == "req-123"

    def test_timeout_stored(self):
        err = self._make("req-123", 10.0)
        assert err.timeout == 10.0

    def test_is_sdk_error(self):
        from orb.sdk.exceptions import RequestTimeoutError, SDKError

        assert issubclass(RequestTimeoutError, SDKError)


# ---------------------------------------------------------------------------
# ParameterMapper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParameterMapperGlobalMappings:
    """map_parameters applies GLOBAL_MAPPINGS when target param exists in handler."""

    @dataclass
    class _RequestCmd:
        requested_count: int
        template_id: str = ""

    def test_count_mapped_to_requested_count_for_dataclass(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        result = ParameterMapper.map_parameters(
            self._RequestCmd, {"count": 5, "template_id": "t-1"}
        )
        assert "requested_count" in result
        assert result["requested_count"] == 5
        assert "count" not in result

    def test_count_not_mapped_when_requested_count_not_in_handler(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        @dataclass
        class OtherCmd:
            template_id: str

        result = ParameterMapper.map_parameters(OtherCmd, {"count": 5, "template_id": "t-1"})
        # count should NOT be mapped because requested_count not in OtherCmd
        assert "count" in result
        assert "requested_count" not in result

    def test_existing_requested_count_not_overwritten_by_count(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        result = ParameterMapper.map_parameters(
            self._RequestCmd, {"count": 3, "requested_count": 10}
        )
        # When both are present, existing requested_count wins (not overwritten)
        assert result["requested_count"] == 10
        # count is still in kwargs (it stays because cqrs_name was already present)
        assert "count" in result

    def test_unknown_params_passed_through_unchanged(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        result = ParameterMapper.map_parameters(
            self._RequestCmd, {"unknown_param": "val", "template_id": "t"}
        )
        assert result["unknown_param"] == "val"


@pytest.mark.unit
class TestParameterMapperCommandMappings:
    """COMMAND_MAPPINGS applied for CreateRequestCommand."""

    def test_create_request_command_count_mapped(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        @dataclass
        class CreateRequestCommand:
            requested_count: int
            template_id: str = ""

        result = ParameterMapper.map_parameters(CreateRequestCommand, {"count": 7})
        assert result.get("requested_count") == 7
        assert "count" not in result

    def test_non_matching_command_name_skips_command_mappings(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        @dataclass
        class UpdateMachineCommand:
            machine_id: str

        result = ParameterMapper.map_parameters(
            UpdateMachineCommand, {"count": 3, "machine_id": "m-1"}
        )
        # No mapping for UpdateMachineCommand, count stays
        assert "count" in result


@pytest.mark.unit
class TestParameterExistsInHandler:
    """_parameter_exists_in_handler checks dataclass fields, Pydantic models, and __init__."""

    def test_detects_dataclass_field(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        @dataclass
        class Cmd:
            requested_count: int

        assert ParameterMapper._parameter_exists_in_handler(Cmd, "requested_count") is True
        assert ParameterMapper._parameter_exists_in_handler(Cmd, "nonexistent") is False

    def test_detects_pydantic_model_field(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        class PydanticCmd(BaseModel):
            requested_count: int = 0

        assert ParameterMapper._parameter_exists_in_handler(PydanticCmd, "requested_count") is True
        assert ParameterMapper._parameter_exists_in_handler(PydanticCmd, "missing") is False

    def test_detects_init_parameter(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        class PlainCmd:
            def __init__(self, requested_count: int, name: str = "") -> None:
                pass

        assert ParameterMapper._parameter_exists_in_handler(PlainCmd, "requested_count") is True
        assert ParameterMapper._parameter_exists_in_handler(PlainCmd, "unknown") is False

    def test_returns_false_for_class_without_init_or_fields(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        class Bare:
            pass

        # Bare still has __init__ from object, won't have custom params
        result = ParameterMapper._parameter_exists_in_handler(Bare, "anything")
        assert result is False


@pytest.mark.unit
class TestGetSupportedParameters:
    """get_supported_parameters returns all addressable parameter names."""

    def test_includes_direct_params_for_dataclass(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        @dataclass
        class Cmd:
            requested_count: int
            template_id: str = ""

        supported = ParameterMapper.get_supported_parameters(Cmd)
        assert "requested_count" in supported
        assert "template_id" in supported

    def test_includes_cli_alias_for_mapped_field(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        @dataclass
        class Cmd:
            requested_count: int

        supported = ParameterMapper.get_supported_parameters(Cmd)
        # "count" maps to "requested_count" via GLOBAL_MAPPINGS
        assert "count" in supported
        assert supported["count"] == "requested_count"

    def test_excludes_cli_alias_when_target_missing(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        @dataclass
        class Cmd:
            template_id: str = ""

        supported = ParameterMapper.get_supported_parameters(Cmd)
        # "count" maps to "requested_count" but that field is not in Cmd
        assert "count" not in supported

    def test_command_specific_mappings_included(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        @dataclass
        class CreateRequestCommand:
            requested_count: int
            template_id: str = ""

        supported = ParameterMapper.get_supported_parameters(CreateRequestCommand)
        assert "count" in supported

    def test_pydantic_model_fields_included(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        class PydanticQuery(BaseModel):
            limit: int = 10
            offset: int = 0

        supported = ParameterMapper.get_supported_parameters(PydanticQuery)
        assert "limit" in supported
        assert "offset" in supported

    def test_no_direct_params_for_empty_class_returns_partial(self):
        from orb.sdk.parameter_mapping import ParameterMapper

        class EmptyCmd:
            pass

        supported = ParameterMapper.get_supported_parameters(EmptyCmd)
        # No dataclass fields, no pydantic fields — should return empty or minimal
        assert isinstance(supported, dict)
