"""Unit tests for SDKMethodDiscovery command output extraction."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.sdk.discovery import SDKMethodDiscovery

# ---------------------------------------------------------------------------
# _extract_command_output
# ---------------------------------------------------------------------------


class TestExtractCommandOutput:
    def setup_method(self):
        self.discovery = SDKMethodDiscovery()

    def test_create_request_command_returns_request_id(self):
        cmd = MagicMock()
        cmd.__class__.__name__ = "CreateRequestCommand"
        cmd.created_request_id = "req-abc"
        result = self.discovery._extract_command_output(cmd)
        assert result == {"created_request_id": "req-abc"}

    def test_create_request_command_returns_none_when_field_not_populated(self):
        cmd = MagicMock()
        cmd.__class__.__name__ = "CreateRequestCommand"
        cmd.created_request_id = None
        result = self.discovery._extract_command_output(cmd)
        assert result is None

    def test_create_return_request_command_returns_all_fields(self):
        cmd = MagicMock()
        cmd.__class__.__name__ = "CreateReturnRequestCommand"
        cmd.created_request_ids = ["ret-1", "ret-2"]
        cmd.processed_machines = ["m-1"]
        cmd.skipped_machines = [{"id": "m-2", "reason": "already returned"}]
        result = self.discovery._extract_command_output(cmd)
        assert result == {
            "created_request_ids": ["ret-1", "ret-2"],
            "processed_machines": ["m-1"],
            "skipped_machines": [{"id": "m-2", "reason": "already returned"}],
        }

    def test_create_return_request_command_omits_none_fields(self):
        cmd = MagicMock()
        cmd.__class__.__name__ = "CreateReturnRequestCommand"
        cmd.created_request_ids = ["ret-1"]
        cmd.processed_machines = None
        cmd.skipped_machines = None
        result = self.discovery._extract_command_output(cmd)
        assert result == {"created_request_ids": ["ret-1"]}

    def test_unknown_command_returns_none(self):
        cmd = MagicMock()
        cmd.__class__.__name__ = "SomeOtherCommand"
        result = self.discovery._extract_command_output(cmd)
        assert result is None

    def test_cleanup_old_requests_command_returns_counts(self):
        cmd = MagicMock()
        cmd.__class__.__name__ = "CleanupOldRequestsCommand"
        cmd.requests_cleaned = 5
        cmd.request_ids_found = ["r-1", "r-2"]
        result = self.discovery._extract_command_output(cmd)
        assert result == {"requests_cleaned": 5, "request_ids_found": ["r-1", "r-2"]}

    def test_create_template_command_success_returns_created_true(self):
        cmd = MagicMock()
        cmd.__class__.__name__ = "CreateTemplateCommand"
        cmd.created = True
        cmd.validation_errors = None
        result = self.discovery._extract_command_output(cmd)
        assert result == {"created": True}

    def test_create_template_command_validation_failure(self):
        cmd = MagicMock()
        cmd.__class__.__name__ = "CreateTemplateCommand"
        cmd.created = False
        cmd.validation_errors = ["bad config"]
        result = self.discovery._extract_command_output(cmd)
        assert result == {"created": False, "validation_errors": ["bad config"]}

    def test_update_template_command_success(self):
        cmd = MagicMock()
        cmd.__class__.__name__ = "UpdateTemplateCommand"
        cmd.updated = True
        cmd.validation_errors = None
        result = self.discovery._extract_command_output(cmd)
        assert result == {"updated": True}

    def test_delete_template_command_success(self):
        cmd = MagicMock()
        cmd.__class__.__name__ = "DeleteTemplateCommand"
        cmd.deleted = True
        result = self.discovery._extract_command_output(cmd)
        assert result == {"deleted": True}


# ---------------------------------------------------------------------------
# _create_command_method_cqrs — integration with output extraction
# ---------------------------------------------------------------------------


class TestCreateCommandMethodCqrs:
    def setup_method(self):
        self.discovery = SDKMethodDiscovery()

    def _make_method_info(self, name="create_request"):
        from orb.sdk.discovery import MethodInfo

        return MethodInfo(
            name=name,
            description="test",
            parameters={},
            required_params=[],
            return_type=None,
            handler_type="command",
            original_class=MagicMock,
        )

    @pytest.mark.asyncio
    async def test_create_request_returns_request_id_dict(self):
        from orb.application.dto.commands import CreateRequestCommand

        # Simulate handler mutating command.created_request_id
        async def fake_execute(cmd):
            cmd.created_request_id = "req-xyz"

        command_bus = MagicMock()
        command_bus.execute = fake_execute

        method = self.discovery._create_command_method_cqrs(
            command_bus, CreateRequestCommand, self._make_method_info("create_request")
        )

        result = await method(template_id="tmpl-1", requested_count=2)

        assert result == {"created_request_id": "req-xyz"}

    @pytest.mark.asyncio
    async def test_create_return_request_returns_output_fields(self):
        from orb.application.dto.commands import CreateReturnRequestCommand

        async def fake_execute(cmd):
            cmd.created_request_ids = ["ret-1"]
            cmd.processed_machines = ["m-1"]
            cmd.skipped_machines = []

        command_bus = MagicMock()
        command_bus.execute = fake_execute

        method = self.discovery._create_command_method_cqrs(
            command_bus,
            CreateReturnRequestCommand,
            self._make_method_info("create_return_request"),
        )

        result = await method(machine_ids=["m-1"])

        assert result["created_request_ids"] == ["ret-1"]
        assert result["processed_machines"] == ["m-1"]

    @pytest.mark.asyncio
    async def test_void_command_returns_none(self):
        from orb.application.dto.commands import CancelRequestCommand

        command_bus = AsyncMock()
        command_bus.execute.return_value = None

        method = self.discovery._create_command_method_cqrs(
            command_bus, CancelRequestCommand, self._make_method_info("cancel_request")
        )

        result = await method(request_id="req-1", reason="test")

        assert result is None
