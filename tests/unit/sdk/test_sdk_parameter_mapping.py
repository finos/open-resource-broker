"""Unit tests for ParameterMapper and SDKMethodDiscovery."""

from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.sdk.exceptions import HandlerDiscoveryError, MethodExecutionError
from orb.sdk.parameter_mapping import ParameterMapper

# ---------------------------------------------------------------------------
# Minimal fake command/query types for testing
# ---------------------------------------------------------------------------


@dataclass
class FakeCreateCommand:
    template_id: str
    requested_count: int
    timeout: Optional[int] = 3600


@dataclass
class FakeListQuery:
    provider_name: Optional[str] = None
    active_only: bool = True


# ---------------------------------------------------------------------------
# ParameterMapper
# ---------------------------------------------------------------------------


class TestParameterMapper:
    def test_passthrough_when_no_mapping_needed(self):
        result = ParameterMapper.map_parameters(FakeListQuery, {"active_only": False})
        assert result == {"active_only": False}

    def test_global_count_maps_to_requested_count(self):
        result = ParameterMapper.map_parameters(
            FakeCreateCommand, {"template_id": "t1", "count": 5}
        )
        assert "requested_count" in result
        assert result["requested_count"] == 5
        assert "count" not in result

    def test_explicit_requested_count_not_overwritten(self):
        result = ParameterMapper.map_parameters(
            FakeCreateCommand,
            {"template_id": "t1", "count": 5, "requested_count": 10},
        )
        # Both present — count should NOT overwrite the explicit requested_count
        assert result["requested_count"] == 10

    def test_unknown_param_passed_through(self):
        result = ParameterMapper.map_parameters(FakeListQuery, {"unknown_param": "x"})
        assert result["unknown_param"] == "x"

    def test_global_mapping_skipped_when_target_absent(self):
        # FakeListQuery has no requested_count field — count should stay as-is
        result = ParameterMapper.map_parameters(FakeListQuery, {"count": 3})
        assert result.get("count") == 3
        assert "requested_count" not in result

    def test_parameter_exists_in_dataclass(self):
        assert ParameterMapper._parameter_exists_in_handler(FakeCreateCommand, "template_id")
        assert not ParameterMapper._parameter_exists_in_handler(FakeCreateCommand, "nonexistent")

    def test_get_supported_parameters_includes_direct_and_mapped(self):
        supported = ParameterMapper.get_supported_parameters(FakeCreateCommand)
        # Direct params
        assert "template_id" in supported
        assert "requested_count" in supported
        # Mapped alias
        assert "count" in supported
        assert supported["count"] == "requested_count"

    def test_get_supported_parameters_no_spurious_mappings(self):
        supported = ParameterMapper.get_supported_parameters(FakeListQuery)
        # FakeListQuery has no requested_count, so 'count' alias must not appear
        assert "count" not in supported


# ---------------------------------------------------------------------------
# SDKMethodDiscovery — name conversion
# ---------------------------------------------------------------------------


class TestSDKMethodDiscoveryNameConversion:
    def setup_method(self):
        from orb.sdk.discovery import SDKMethodDiscovery

        self.disc = SDKMethodDiscovery()

    def test_query_name_strips_query_suffix(self):
        class ListTemplatesQuery:
            pass

        assert self.disc._query_to_method_name(ListTemplatesQuery) == "list_templates"

    def test_query_name_no_suffix(self):
        class GetRequest:
            pass

        assert self.disc._query_to_method_name(GetRequest) == "get_request"

    def test_command_name_strips_command_suffix(self):
        class CreateRequestCommand:
            pass

        assert self.disc._command_to_method_name(CreateRequestCommand) == "create_request"

    def test_camel_to_snake(self):
        disc = self.disc
        assert disc._camel_to_snake("GetProviderHealth") == "get_provider_health"
        assert disc._camel_to_snake("ListActiveRequests") == "list_active_requests"
        assert disc._camel_to_snake("Simple") == "simple"

    def test_generate_description_contains_operation_type(self):
        desc = self.disc._generate_method_description("list_templates", "query")
        assert "query" in desc.lower() or "Query" in desc


# ---------------------------------------------------------------------------
# SDKMethodDiscovery — _create_method_info with dataclass fields
# ---------------------------------------------------------------------------


class TestSDKMethodDiscoveryMethodInfo:
    def setup_method(self):
        from orb.sdk.discovery import SDKMethodDiscovery

        self.disc = SDKMethodDiscovery()

    def test_required_fields_detected(self):
        info = self.disc._create_method_info(
            "create_fake", FakeCreateCommand, MagicMock(), "command"
        )
        assert "template_id" in info.required_params
        assert "requested_count" in info.required_params

    def test_optional_fields_not_required(self):
        info = self.disc._create_method_info(
            "create_fake", FakeCreateCommand, MagicMock(), "command"
        )
        assert "timeout" not in info.required_params

    def test_handler_type_stored(self):
        info = self.disc._create_method_info("list_fake", FakeListQuery, MagicMock(), "query")
        assert info.handler_type == "query"
        assert info.original_class is FakeListQuery

    def test_fallback_on_bad_type(self):
        # Passing a non-dataclass, non-pydantic type should not raise — falls back
        class Weird:
            pass

        info = self.disc._create_method_info("weird", Weird, MagicMock(), "query")
        assert info.name == "weird"


# ---------------------------------------------------------------------------
# SDKMethodDiscovery — created methods execute via bus
# ---------------------------------------------------------------------------


class TestSDKMethodDiscoveryCreatedMethods:
    def setup_method(self):
        from orb.sdk.discovery import MethodInfo, SDKMethodDiscovery

        self.disc = SDKMethodDiscovery()
        self.method_info = MethodInfo(
            name="list_fake",
            description="test",
            parameters={},
            required_params=[],
            return_type=None,
            handler_type="query",
            original_class=FakeListQuery,
        )

    @pytest.mark.asyncio
    async def test_query_method_calls_bus_execute(self):
        query_bus = AsyncMock()
        query_bus.execute.return_value = None

        method = self.disc._create_query_method_cqrs(query_bus, FakeListQuery, self.method_info)
        await method(active_only=False)

        query_bus.execute.assert_awaited_once()
        call_arg = query_bus.execute.call_args[0][0]
        assert isinstance(call_arg, FakeListQuery)
        assert call_arg.active_only is False

    @pytest.mark.asyncio
    async def test_query_method_raises_method_execution_error_on_failure(self):
        query_bus = AsyncMock()
        query_bus.execute.side_effect = RuntimeError("bus exploded")

        method = self.disc._create_query_method_cqrs(query_bus, FakeListQuery, self.method_info)
        with pytest.raises(MethodExecutionError, match="list_fake"):
            await method()

    @pytest.mark.asyncio
    async def test_command_method_calls_bus_execute(self):
        from orb.sdk.discovery import MethodInfo

        cmd_info = MethodInfo(
            name="create_fake",
            description="test",
            parameters={},
            required_params=[],
            return_type=None,
            handler_type="command",
            original_class=FakeCreateCommand,
        )
        command_bus = AsyncMock()
        command_bus.execute.return_value = None

        method = self.disc._create_command_method_cqrs(command_bus, FakeCreateCommand, cmd_info)
        await method(template_id="t1", requested_count=2)

        command_bus.execute.assert_awaited_once()
        call_arg = command_bus.execute.call_args[0][0]
        assert isinstance(call_arg, FakeCreateCommand)
        assert call_arg.template_id == "t1"

    @pytest.mark.asyncio
    async def test_command_method_raises_method_execution_error_on_failure(self):
        from orb.sdk.discovery import MethodInfo

        cmd_info = MethodInfo(
            name="create_fake",
            description="test",
            parameters={},
            required_params=[],
            return_type=None,
            handler_type="command",
            original_class=FakeCreateCommand,
        )
        command_bus = AsyncMock()
        command_bus.execute.side_effect = ValueError("bad value")

        method = self.disc._create_command_method_cqrs(command_bus, FakeCreateCommand, cmd_info)
        with pytest.raises(MethodExecutionError, match="create_fake"):
            await method(template_id="t1", requested_count=2)


# ---------------------------------------------------------------------------
# SDKMethodDiscovery — discover_cqrs_methods integration
# ---------------------------------------------------------------------------


class TestDiscoverCQRSMethods:
    @pytest.mark.asyncio
    async def test_discover_returns_methods_dict(self):
        from unittest.mock import patch

        from orb.sdk.discovery import SDKMethodDiscovery

        disc = SDKMethodDiscovery()
        query_bus = AsyncMock()
        command_bus = AsyncMock()

        fake_query_handlers = {FakeListQuery: MagicMock()}
        fake_command_handlers = {FakeCreateCommand: MagicMock()}

        with (
            patch("sdk.discovery.get_registered_query_handlers", return_value=fake_query_handlers),
            patch(
                "sdk.discovery.get_registered_command_handlers", return_value=fake_command_handlers
            ),
        ):
            methods = await disc.discover_cqrs_methods(query_bus, command_bus)

        assert "list_fake" in methods or any("fake" in k for k in methods)
        assert callable(list(methods.values())[0])

    @pytest.mark.asyncio
    async def test_discover_raises_handler_discovery_error_on_failure(self):
        from unittest.mock import patch

        from orb.sdk.discovery import SDKMethodDiscovery

        disc = SDKMethodDiscovery()

        with patch("sdk.discovery.get_registered_query_handlers", side_effect=RuntimeError("boom")):
            with pytest.raises(HandlerDiscoveryError):
                await disc.discover_cqrs_methods(AsyncMock(), AsyncMock())


# ---------------------------------------------------------------------------
# SDKMethodDiscovery — standardize_return_type
# ---------------------------------------------------------------------------


class TestStandardizeReturnType:
    def setup_method(self):
        from orb.sdk.discovery import SDKMethodDiscovery

        self.disc = SDKMethodDiscovery()

    def test_none_returns_none(self):
        assert self.disc._standardize_return_type(None) is None

    def test_dict_returned_as_is(self):
        result = self.disc._standardize_return_type({"key": "val"})
        assert result == {"key": "val"}

    def test_object_with_to_dict_converted(self):
        obj = MagicMock()
        obj.to_dict.return_value = {"id": "123"}
        result = self.disc._standardize_return_type(obj)
        assert result == {"id": "123"}

    def test_list_of_dtos_converted(self):
        obj = MagicMock()
        obj.to_dict.return_value = {"id": "1"}
        result = self.disc._standardize_return_type([obj])
        assert result == [{"id": "1"}]

    def test_primitive_returned_unchanged(self):
        assert self.disc._standardize_return_type(42) == 42
        assert self.disc._standardize_return_type("hello") == "hello"

    def test_datetime_values_serialized_to_iso(self):
        from datetime import datetime, timezone

        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = self.disc._standardize_return_type({"created_at": dt})
        assert result["created_at"] == "2024-01-15T12:00:00+00:00"
