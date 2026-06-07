"""Tests for template command handlers."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock, patch


def _make_args(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _make_container(command_bus=None, query_bus=None, orchestrator=None):
    container = MagicMock()

    from orb.application.dto.interface_response import InterfaceResponse
    from orb.application.services.orchestration.create_template import CreateTemplateOrchestrator
    from orb.infrastructure.di.buses import CommandBus, QueryBus
    from orb.interface.response_formatting_service import ResponseFormattingService

    mock_formatter = MagicMock(spec=ResponseFormattingService)
    mock_formatter.format_template_mutation.return_value = InterfaceResponse(data={"success": True})

    def _get(cls):
        if cls is CommandBus:
            return command_bus
        if cls is QueryBus:
            return query_bus
        if cls is CreateTemplateOrchestrator:
            return orchestrator
        if cls is ResponseFormattingService:
            return mock_formatter
        return MagicMock()

    container.get.side_effect = _get
    return container


class TestHandleCreateTemplateValidateOnly:
    """Tests for the --validate-only flag in handle_create_template."""

    def _make_template_file(self, tmp_path) -> str:
        import json

        data = {
            "template_id": "tmpl-001",
            "provider_api": "aws",
            "image_id": "ami-12345",
            "name": "Test Template",
        }
        p = tmp_path / "template.json"
        p.write_text(json.dumps(data))
        return str(p)

    def test_validate_only_does_not_execute_command_bus(self, tmp_path):
        import asyncio

        from orb.interface.template_command_handlers import handle_create_template

        command_bus = MagicMock()
        command_bus.execute = AsyncMock()
        container = _make_container(command_bus=command_bus)

        args = _make_args(
            file=self._make_template_file(tmp_path),
            validate_only=True,
        )

        with patch("orb.interface.template_command_handlers.get_container", return_value=container):
            with patch(
                "orb.infrastructure.mocking.dry_run_context.is_dry_run_active",
                return_value=False,
            ):
                result = asyncio.run(handle_create_template(args))

        command_bus.execute.assert_not_called()
        assert result["validate_only"] is True
        assert result["success"] is True
        assert result["template_id"] == "tmpl-001"

    def test_validate_only_false_executes_command_bus(self, tmp_path):
        import asyncio

        from orb.application.services.orchestration.create_template import (
            CreateTemplateOrchestrator,
        )
        from orb.application.services.orchestration.dtos import CreateTemplateOutput
        from orb.interface.template_command_handlers import handle_create_template

        mock_orchestrator = MagicMock(spec=CreateTemplateOrchestrator)
        mock_orchestrator.execute = AsyncMock(
            return_value=CreateTemplateOutput(
                template_id="tmpl-001", created=True, validation_errors=[]
            )
        )
        container = _make_container(orchestrator=mock_orchestrator)

        args = _make_args(
            file=self._make_template_file(tmp_path),
            validate_only=False,
        )

        with patch("orb.interface.template_command_handlers.get_container", return_value=container):
            with patch(
                "orb.infrastructure.mocking.dry_run_context.is_dry_run_active",
                return_value=False,
            ):
                result = asyncio.run(handle_create_template(args))

        mock_orchestrator.execute.assert_called_once()
        assert result.data["success"] is True
        assert result.data.get("validate_only") is None


class TestHandleCreateTemplateDefaultsFirst:
    """Tests for defaults-first provider_api behavior in CLI create flow."""

    def _make_template_file(self, tmp_path, data: dict) -> str:
        import json

        p = tmp_path / "template.json"
        p.write_text(json.dumps(data))
        return str(p)

    def test_create_rejects_missing_provider_api_without_oci_context(self, tmp_path):
        import asyncio

        from orb.interface.template_command_handlers import handle_create_template

        template_file = self._make_template_file(
            tmp_path,
            {
                "template_id": "tmpl-oci-001",
                "image_id": "ocid1.image.oc1..example",
            },
        )

        args = _make_args(file=template_file, validate_only=False)

        with patch(
            "orb.infrastructure.mocking.dry_run_context.is_dry_run_active",
            return_value=False,
        ):
            result = asyncio.run(handle_create_template(args))

        assert result.exit_code == 1
        assert result.data["error"] == "provider_api is required in template file"

    def test_create_passes_provider_name_context(self, tmp_path):
        import asyncio

        from orb.application.services.orchestration.create_template import (
            CreateTemplateOrchestrator,
        )
        from orb.application.services.orchestration.dtos import CreateTemplateOutput
        from orb.interface.template_command_handlers import handle_create_template

        template_file = self._make_template_file(
            tmp_path,
            {
                "template_id": "tmpl-oci-ctx",
                "provider_name": "oci-primary",
                "image_id": "ocid1.image.oc1..example",
            },
        )

        mock_orchestrator = MagicMock(spec=CreateTemplateOrchestrator)
        mock_orchestrator.execute = AsyncMock(
            return_value=CreateTemplateOutput(
                template_id="tmpl-oci-ctx",
                created=True,
                validation_errors=[],
            )
        )
        container = _make_container(orchestrator=mock_orchestrator)
        args = _make_args(file=template_file, validate_only=False)

        with patch("orb.interface.template_command_handlers.get_container", return_value=container):
            with patch(
                "orb.infrastructure.mocking.dry_run_context.is_dry_run_active",
                return_value=False,
            ):
                asyncio.run(handle_create_template(args))

        mock_orchestrator.execute.assert_called_once()
        create_input = mock_orchestrator.execute.call_args.args[0]
        assert create_input.provider_name == "oci-primary"

    def test_create_allows_image_id_from_provider_defaults(self, tmp_path):
        import asyncio

        from orb.application.services.orchestration.create_template import (
            CreateTemplateOrchestrator,
        )
        from orb.application.services.orchestration.dtos import CreateTemplateOutput
        from orb.interface.template_command_handlers import handle_create_template

        template_file = self._make_template_file(
            tmp_path,
            {
                "template_id": "oci-vm-flex-ondemand-small",
                "provider_api": "OCICompute",
                "provider_name": "oci-default",
            },
        )

        mock_orchestrator = MagicMock(spec=CreateTemplateOrchestrator)
        mock_orchestrator.execute = AsyncMock(
            return_value=CreateTemplateOutput(
                template_id="oci-vm-flex-ondemand-small",
                created=True,
                validation_errors=[],
            )
        )
        container = _make_container(orchestrator=mock_orchestrator)
        args = _make_args(file=template_file, provider="oci-default", validate_only=False)

        with patch("orb.interface.template_command_handlers.get_container", return_value=container):
            with patch(
                "orb.infrastructure.mocking.dry_run_context.is_dry_run_active",
                return_value=False,
            ):
                result = asyncio.run(handle_create_template(args))

        assert result.data["success"] is True
        create_input = mock_orchestrator.execute.call_args.args[0]
        assert create_input.image_id is None
        assert create_input.provider_name == "oci-default"
