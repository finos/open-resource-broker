"""Template command handlers for CLI interface.

This module provides the interface layer handlers for template operations,
using orchestrators for architectural consistency.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from orb.application.dto.interface_response import InterfaceResponse
from orb.domain.base.exceptions import DuplicateError, EntityNotFoundError
from orb.infrastructure.error.decorators import handle_interface_exceptions

if TYPE_CHECKING:
    import argparse

    from orb.application.ports.query_bus_port import QueryBusPort
    from orb.application.services.orchestration.create_template import CreateTemplateOrchestrator
    from orb.application.services.orchestration.delete_template import DeleteTemplateOrchestrator
    from orb.application.services.orchestration.get_template import GetTemplateOrchestrator
    from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
    from orb.application.services.orchestration.refresh_templates import (
        RefreshTemplatesOrchestrator,
    )
    from orb.application.services.orchestration.update_template import UpdateTemplateOrchestrator
    from orb.application.services.orchestration.validate_template import (
        ValidateTemplateOrchestrator,
    )
    from orb.domain.base.ports.console_port import ConsolePort
    from orb.interface.response_formatting_service import ResponseFormattingService


class TemplateReadHandlers:
    """CLI handlers that read and validate compute templates."""

    def __init__(
        self,
        list_orchestrator: ListTemplatesOrchestrator,
        get_orchestrator: GetTemplateOrchestrator,
        validate_orchestrator: ValidateTemplateOrchestrator,
        query_bus: QueryBusPort,
        formatter: ResponseFormattingService,
        console: ConsolePort,
    ) -> None:
        self._list_orchestrator = list_orchestrator
        self._get_orchestrator = get_orchestrator
        self._validate_orchestrator = validate_orchestrator
        self._query_bus = query_bus
        self._formatter = formatter
        self._console = console

    @handle_interface_exceptions(context="list_templates", interface_type="cli")
    async def list_templates(self, args: argparse.Namespace) -> dict[str, Any] | InterfaceResponse:
        """List compute templates, optionally filtered by provider and active status, with pagination."""
        from orb.application.services.orchestration.dtos import ListTemplatesInput
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        entry = OPERATION_CATALOG["list_templates"]
        orchestrator = self._list_orchestrator
        formatter = self._formatter

        if hasattr(args, "input_data") and args.input_data:
            input_data = args.input_data
            provider_name = input_data.get("provider_api") or input_data.get("provider_name")
            provider_type = input_data.get("provider_type")
            provider_api = input_data.get("provider_api")
            active_only = input_data.get("active_only", True)
            limit = input_data.get("limit") or 50
            offset = input_data.get("offset") or 0
        else:
            provider_name = getattr(args, "provider_name", None)
            provider_type = getattr(args, "provider_type", None)
            provider_api = getattr(args, "provider_api", None)
            active_only = getattr(args, "active_only", True)
            # argparse leaves --limit/--offset as None when omitted; coerce here
            # so the orchestrator never sees None where it expects int.
            limit = getattr(args, "limit", None) or 50
            offset = getattr(args, "offset", None) or 0

        result = await orchestrator.execute(
            ListTemplatesInput(
                active_only=active_only,
                provider_name=provider_name,
                provider_type=provider_type,
                provider_api=provider_api,
                limit=limit,
                offset=offset,
                filter_expressions=getattr(args, "filter", None) or [],
            )
        )

        if not result.templates:
            from orb.cli.help_utils import print_getting_started_help

            console = self._console
            console.info("")
            console.info("No templates found.")
            console.info("")
            print_getting_started_help()

        return entry.renderer_for(Interface.CLI)(formatter, result)

    @handle_interface_exceptions(context="get_template", interface_type="cli")
    async def get_template(self, args: argparse.Namespace) -> dict[str, Any] | InterfaceResponse:
        """Retrieve a single compute template by its template ID."""
        from orb.application.services.orchestration.dtos import GetTemplateInput
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        template_id = getattr(args, "template_id", None) or getattr(args, "flag_template_id", None)
        if not template_id:
            return InterfaceResponse(
                data={"success": False, "error": "Template ID is required", "template": None},
                exit_code=1,
            )

        entry = OPERATION_CATALOG["get_template"]
        orchestrator = self._get_orchestrator
        formatter = self._formatter

        provider_name = getattr(args, "provider_name", None)
        try:
            result = await orchestrator.execute(
                GetTemplateInput(template_id=template_id, provider_name=provider_name)
            )
        except EntityNotFoundError:
            return formatter.format_error(f"Template '{template_id}' not found")

        if not result.template:
            return InterfaceResponse(
                data={
                    "success": False,
                    "error": f"Template '{template_id}' not found",
                    "template": None,
                },
                exit_code=1,
            )

        return entry.renderer_for(Interface.CLI)(formatter, result)

    @handle_interface_exceptions(context="validate_template", interface_type="cli")
    async def validate_template(
        self, args: argparse.Namespace
    ) -> dict[str, Any] | InterfaceResponse:
        """Validate a compute template by ID, from a file, or all templates with --all."""
        from orb.application.services.orchestration.dtos import ValidateTemplateInput
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        entry = OPERATION_CATALOG["validate_template"]
        orchestrator = self._validate_orchestrator
        formatter = self._formatter
        render = entry.renderer_for(Interface.CLI)

        if hasattr(args, "all") and args.all:
            from orb.application.services.orchestration.dtos import ListTemplatesInput

            list_result = await self._list_orchestrator.execute(ListTemplatesInput())

            results = []
            for template in list_result.templates:
                tid = getattr(template, "template_id", None) or (
                    template.get("template_id") if isinstance(template, dict) else None
                )
                val = await orchestrator.execute(ValidateTemplateInput(template_id=tid))
                results.append({"template_id": tid, "valid": val.valid, "errors": val.errors})

            return {
                "success": True,
                "message": f"Validated {len(results)} templates",
                "results": results,
            }

        if hasattr(args, "file") and args.file:
            from pathlib import Path

            import yaml

            template_file = Path(args.file)
            if not template_file.exists():
                return InterfaceResponse(
                    data={
                        "success": False,
                        "error": f"Template file not found: {template_file}",
                        "valid": False,
                    },
                    exit_code=1,
                )

            try:
                with open(template_file) as f:
                    if template_file.suffix.lower() in {".yml", ".yaml"}:
                        template_config = yaml.safe_load(f)
                    else:
                        template_config = json.load(f)
            except Exception as e:
                return InterfaceResponse(
                    data={
                        "success": False,
                        "error": f"Failed to parse template file: {e!s}",
                        "valid": False,
                    },
                    exit_code=1,
                )

            template_id = template_config.get("template_id", "file-template")
            result = await orchestrator.execute(
                ValidateTemplateInput(template_id=template_id, config=template_config)
            )
            return render(formatter, result)

        template_id = getattr(args, "template_id", None) or getattr(args, "flag_template_id", None)
        if template_id:
            try:
                result = await orchestrator.execute(ValidateTemplateInput(template_id=template_id))
            except EntityNotFoundError:
                return formatter.format_error(f"Template '{template_id}' not found")
            return render(formatter, result)

        return InterfaceResponse(
            data={
                "success": False,
                "error": "Must provide either template_id, --file, or --all",
                "valid": False,
            },
            exit_code=1,
        )

    @handle_interface_exceptions(context="get_multiple_templates", interface_type="cli")
    async def get_multiple_templates(
        self, args: argparse.Namespace
    ) -> dict[str, Any] | InterfaceResponse:
        """Fetch multiple templates by ID via GetMultipleTemplatesQuery through the query bus."""
        from orb.application.dto.bulk_queries import GetMultipleTemplatesQuery

        template_ids: list[str] = []
        if hasattr(args, "template_ids") and args.template_ids:
            template_ids.extend(args.template_ids)
        if hasattr(args, "flag_template_ids") and args.flag_template_ids:
            template_ids.extend(args.flag_template_ids)
        if hasattr(args, "flag_ids") and args.flag_ids:
            template_ids.extend(args.flag_ids)

        if not template_ids:
            return {
                "error": "No template IDs provided",
                "message": "At least one template ID required",
            }

        query = GetMultipleTemplatesQuery(
            template_ids=[str(tid) for tid in template_ids],
            active_only=bool(getattr(args, "active_only", True)),
        )
        result = await self._query_bus.execute(query)
        return {
            "templates": result.templates,
            "found_count": result.found_count,
            "not_found_ids": result.not_found_ids,
            "total_requested": result.total_requested,
        }


class TemplateWriteHandlers:
    """CLI handlers that create, update, delete and refresh compute templates."""

    def __init__(
        self,
        create_orchestrator: CreateTemplateOrchestrator,
        update_orchestrator: UpdateTemplateOrchestrator,
        delete_orchestrator: DeleteTemplateOrchestrator,
        refresh_orchestrator: RefreshTemplatesOrchestrator,
        formatter: ResponseFormattingService,
    ) -> None:
        self._create_orchestrator = create_orchestrator
        self._update_orchestrator = update_orchestrator
        self._delete_orchestrator = delete_orchestrator
        self._refresh_orchestrator = refresh_orchestrator
        self._formatter = formatter

    @handle_interface_exceptions(context="create_template", interface_type="cli")
    async def create_template(self, args: argparse.Namespace) -> dict[str, Any] | InterfaceResponse:
        """Handle create template operations using the CreateTemplateOrchestrator."""
        from orb.application.services.orchestration.dtos import CreateTemplateInput
        from orb.infrastructure.mocking.dry_run_context import is_dry_run_active
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        if is_dry_run_active():
            return {
                "success": True,
                "message": "DRY-RUN: Template creation would be executed",
                "template_id": getattr(args, "template_id", "dry-run-template"),
                "dry_run": True,
            }

        if not hasattr(args, "file") or not args.file:
            return InterfaceResponse(
                data={"success": False, "error": "Template file is required"}, exit_code=1
            )

        try:
            with open(args.file) as f:
                template_config = json.load(f)
        except FileNotFoundError:
            return InterfaceResponse(
                data={"success": False, "error": f"Template file not found: {args.file}"},
                exit_code=1,
            )
        except json.JSONDecodeError as e:
            return InterfaceResponse(
                data={"success": False, "error": f"Invalid JSON in template file: {e}"}, exit_code=1
            )

        template_id = template_config.get("template_id") or template_config.get("templateId")
        if not template_id:
            return InterfaceResponse(
                data={"success": False, "error": "template_id is required in template file"},
                exit_code=1,
            )

        provider_api = template_config.get("provider_api") or template_config.get("providerApi")
        if not provider_api:
            return InterfaceResponse(
                data={"success": False, "error": "provider_api is required in template file"},
                exit_code=1,
            )

        image_id = (
            template_config.get("machine_image")
            or template_config.get("image_id")
            or template_config.get("imageId")
        )

        if getattr(args, "validate_only", False):
            return {
                "success": True,
                "message": f"Template {template_id} is valid (not created)",
                "template_id": template_id,
                "validate_only": True,
            }

        entry = OPERATION_CATALOG["create_template"]
        orchestrator = self._create_orchestrator
        formatter = self._formatter

        try:
            result = await orchestrator.execute(
                CreateTemplateInput(
                    template_id=template_id,
                    provider_api=provider_api,
                    image_id=image_id,
                    name=template_config.get("name"),
                    description=template_config.get("description"),
                    machine_type=template_config.get("machine_type")
                    or template_config.get("machineType"),
                    instance_type=template_config.get("instance_type")
                    or template_config.get("instanceType"),
                    tags=template_config.get("tags", {}),
                    configuration=template_config,
                )
            )
        except DuplicateError:
            return InterfaceResponse(
                data={
                    "success": False,
                    "error": f"Template '{template_id}' already exists",
                    "template_id": template_id,
                },
                exit_code=1,
            )

        if result.validation_errors:
            return InterfaceResponse(
                data={
                    "success": False,
                    "error": f"Template validation failed: {', '.join(result.validation_errors)}",
                    "template_id": template_id,
                },
                exit_code=1,
            )

        return entry.renderer_for(Interface.CLI)(formatter, result)

    @handle_interface_exceptions(context="update_template", interface_type="cli")
    async def update_template(self, args: argparse.Namespace) -> dict[str, Any] | InterfaceResponse:
        """Handle update template operations using the UpdateTemplateOrchestrator."""
        from orb.application.services.orchestration.dtos import UpdateTemplateInput
        from orb.infrastructure.mocking.dry_run_context import is_dry_run_active
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        template_id = getattr(args, "template_id", None) or getattr(args, "flag_template_id", None)

        if is_dry_run_active():
            return {
                "success": True,
                "message": f"DRY-RUN: Template {template_id} update would be executed",
                "template_id": template_id,
                "dry_run": True,
            }

        file_path = getattr(args, "file", None)
        if not file_path:
            return InterfaceResponse(
                data={"success": False, "error": "Template file is required"}, exit_code=1
            )

        try:
            with open(file_path) as f:
                template_config = json.load(f)
        except FileNotFoundError:
            return InterfaceResponse(
                data={"success": False, "error": f"Template file not found: {file_path}"},
                exit_code=1,
            )
        except json.JSONDecodeError as e:
            return InterfaceResponse(
                data={"success": False, "error": f"Invalid JSON in template file: {e}"}, exit_code=1
            )

        if not isinstance(template_config, dict):
            return InterfaceResponse(
                data={"success": False, "error": "Template file must contain a JSON object"},
                exit_code=1,
            )

        file_template_id = template_config.get("template_id") or template_config.get("templateId")
        resolved_template_id = template_id or file_template_id
        if not resolved_template_id:
            return InterfaceResponse(
                data={"success": False, "error": "Template ID is required (via arg or file)"},
                exit_code=1,
            )

        entry = OPERATION_CATALOG["update_template"]
        orchestrator = self._update_orchestrator
        formatter = self._formatter

        try:
            result = await orchestrator.execute(
                UpdateTemplateInput(
                    template_id=resolved_template_id,
                    name=template_config.get("name"),
                    description=template_config.get("description"),
                    machine_type=template_config.get("machine_type")
                    or template_config.get("machineType"),
                    instance_type=template_config.get("instance_type"),
                    image_id=template_config.get("machine_image")
                    or template_config.get("image_id"),
                    configuration=template_config,
                )
            )
        except EntityNotFoundError:
            return InterfaceResponse(
                data={
                    "success": False,
                    "error": f"Template '{resolved_template_id}' not found",
                    "template_id": resolved_template_id,
                },
                exit_code=1,
            )

        if result.validation_errors:
            return InterfaceResponse(
                data={
                    "success": False,
                    "error": f"Template validation failed: {', '.join(result.validation_errors)}",
                    "template_id": resolved_template_id,
                },
                exit_code=1,
            )

        return entry.renderer_for(Interface.CLI)(formatter, result)

    @handle_interface_exceptions(context="delete_template", interface_type="cli")
    async def delete_template(self, args: argparse.Namespace) -> dict[str, Any] | InterfaceResponse:
        """Handle delete template operations using the DeleteTemplateOrchestrator."""
        from orb.application.services.orchestration.dtos import DeleteTemplateInput
        from orb.infrastructure.mocking.dry_run_context import is_dry_run_active
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        template_id = getattr(args, "template_id", None) or getattr(args, "flag_template_id", None)
        if not template_id:
            return InterfaceResponse(
                data={"success": False, "error": "Template ID is required"},
                exit_code=1,
            )

        if is_dry_run_active():
            return {
                "success": True,
                "message": f"DRY-RUN: Template {template_id} deletion would be executed",
                "template_id": template_id,
                "dry_run": True,
            }

        entry = OPERATION_CATALOG["delete_template"]
        formatter = self._formatter

        if not getattr(args, "force", False):
            return formatter.format_error(
                "Destructive operation requires --force flag. Use --force to confirm deletion."
            )

        orchestrator = self._delete_orchestrator

        try:
            result = await orchestrator.execute(DeleteTemplateInput(template_id=template_id))
        except EntityNotFoundError:
            return InterfaceResponse(
                data={
                    "success": False,
                    "error": f"Template '{template_id}' not found",
                    "template_id": template_id,
                },
                exit_code=1,
            )

        if not result.deleted:
            return InterfaceResponse(
                data={
                    "success": False,
                    "error": f"Template '{template_id}' could not be deleted",
                    "template_id": template_id,
                },
                exit_code=1,
            )

        return entry.renderer_for(Interface.CLI)(formatter, result)

    @handle_interface_exceptions(context="refresh_templates", interface_type="cli")
    async def refresh_templates(
        self, args: argparse.Namespace
    ) -> dict[str, Any] | InterfaceResponse:
        """Handle refresh templates operations using the RefreshTemplatesOrchestrator."""
        from orb.application.services.orchestration.dtos import RefreshTemplatesInput
        from orb.interface.catalog import OPERATION_CATALOG, Interface

        entry = OPERATION_CATALOG["refresh_templates"]
        orchestrator = self._refresh_orchestrator
        formatter = self._formatter

        provider_name = getattr(args, "provider_name", None) or getattr(args, "provider_api", None)
        result = await orchestrator.execute(RefreshTemplatesInput(provider_name=provider_name))

        return entry.renderer_for(Interface.CLI)(formatter, result)
