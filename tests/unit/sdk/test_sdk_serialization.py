"""Unit tests for SDK custom serialization (raw_response, format)."""

import json
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from orb.sdk.discovery import MethodInfo, SDKMethodDiscovery
from orb.sdk.exceptions import MethodExecutionError, SDKError


@dataclass
class FakeQuery:
    active_only: bool = True


@dataclass
class FakeCommand:
    template_id: str
    requested_count: int = 1
    created_request_id: Optional[str] = None


# Alias so _COMMAND_OUTPUT_FIELDS lookup works (keyed by class __name__)
FakeCommand.__name__ = "CreateRequestCommand"


class TestQuerySerialization:
    def setup_method(self):
        self.disc = SDKMethodDiscovery()
        self.method_info = MethodInfo(
            name="list_fake",
            description="test",
            parameters={},
            required_params=[],
            return_type=None,
            handler_type="query",
            original_class=FakeQuery,
        )

    @pytest.mark.asyncio
    async def test_default_returns_dict(self):
        bus = AsyncMock()
        bus.execute.return_value = [{"id": "1"}, {"id": "2"}]

        method = self.disc._create_query_method_cqrs(bus, FakeQuery, self.method_info)
        result = await method(active_only=True)

        assert isinstance(result, list)
        assert result == [{"id": "1"}, {"id": "2"}]

    @pytest.mark.asyncio
    async def test_raw_response_skips_standardization(self):
        raw_obj = MagicMock()
        raw_obj.to_dict.return_value = {"converted": True}
        bus = AsyncMock()
        bus.execute.return_value = raw_obj

        method = self.disc._create_query_method_cqrs(bus, FakeQuery, self.method_info)
        result = await method(raw_response=True)

        # Should return the raw object, not the dict from to_dict()
        assert result is raw_obj

    @pytest.mark.asyncio
    async def test_format_json(self):
        bus = AsyncMock()
        bus.execute.return_value = {"key": "value", "count": 42}

        method = self.disc._create_query_method_cqrs(bus, FakeQuery, self.method_info)
        result = await method(format="json")

        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed["key"] == "value"
        assert parsed["count"] == 42

    @pytest.mark.asyncio
    async def test_format_yaml(self):
        bus = AsyncMock()
        bus.execute.return_value = {"key": "value", "count": 42}

        method = self.disc._create_query_method_cqrs(bus, FakeQuery, self.method_info)
        result = await method(format="yaml")

        assert isinstance(result, str)
        parsed = yaml.safe_load(result)
        assert parsed["key"] == "value"
        assert parsed["count"] == 42

    @pytest.mark.asyncio
    async def test_format_case_insensitive(self):
        bus = AsyncMock()
        bus.execute.return_value = {"key": "value"}

        method = self.disc._create_query_method_cqrs(bus, FakeQuery, self.method_info)
        result = await method(format="JSON")

        assert isinstance(result, str)
        assert json.loads(result)["key"] == "value"

    @pytest.mark.asyncio
    async def test_unsupported_format_raises(self):
        bus = AsyncMock()
        bus.execute.return_value = {"key": "value"}

        method = self.disc._create_query_method_cqrs(bus, FakeQuery, self.method_info)
        with pytest.raises(MethodExecutionError):
            await method(format="xml")

    @pytest.mark.asyncio
    async def test_raw_response_wins_over_format(self):
        raw_obj = MagicMock()
        bus = AsyncMock()
        bus.execute.return_value = raw_obj

        method = self.disc._create_query_method_cqrs(bus, FakeQuery, self.method_info)
        result = await method(raw_response=True, format="json")

        # raw_response takes precedence
        assert result is raw_obj

    @pytest.mark.asyncio
    async def test_format_not_passed_to_query_constructor(self):
        """format and raw_response must be popped before reaching the CQRS constructor."""
        bus = AsyncMock()
        bus.execute.return_value = None

        method = self.disc._create_query_method_cqrs(bus, FakeQuery, self.method_info)
        # FakeQuery doesn't accept format or raw_response — would raise TypeError if leaked
        await method(active_only=False, format="json", raw_response=False)

        call_arg = bus.execute.call_args[0][0]
        assert isinstance(call_arg, FakeQuery)
        assert call_arg.active_only is False


class TestCommandSerialization:
    def setup_method(self):
        self.disc = SDKMethodDiscovery()
        self.method_info = MethodInfo(
            name="create_fake",
            description="test",
            parameters={},
            required_params=[],
            return_type=None,
            handler_type="command",
            original_class=FakeCommand,
        )

    @pytest.mark.asyncio
    async def test_command_format_json(self):
        bus = AsyncMock()

        # Simulate handler populating output field
        async def set_output(cmd):
            cmd.created_request_id = "req-123"

        bus.execute.side_effect = set_output

        method = self.disc._create_command_method_cqrs(bus, FakeCommand, self.method_info)
        result = await method(template_id="t1", format="json")

        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed["created_request_id"] == "req-123"

    @pytest.mark.asyncio
    async def test_command_raw_response(self):
        bus = AsyncMock()

        async def set_output(cmd):
            cmd.created_request_id = "req-456"

        bus.execute.side_effect = set_output

        method = self.disc._create_command_method_cqrs(bus, FakeCommand, self.method_info)
        result = await method(template_id="t1", raw_response=True)

        # raw_response on commands returns the extracted output dict (not standardized)
        assert result == {"created_request_id": "req-456"}


class TestApplyFormat:
    def setup_method(self):
        self.disc = SDKMethodDiscovery()

    def test_none_format_returns_data(self):
        data = {"key": "value"}
        assert self.disc._apply_format(data, None) is data

    def test_json_format(self):
        result = self.disc._apply_format({"a": 1}, "json")
        assert json.loads(result) == {"a": 1}

    def test_yaml_format(self):
        result = self.disc._apply_format({"a": 1}, "yaml")
        assert yaml.safe_load(result) == {"a": 1}

    def test_unsupported_format_raises_sdk_error(self):
        with pytest.raises(SDKError, match="Unsupported format"):
            self.disc._apply_format({"a": 1}, "xml")
