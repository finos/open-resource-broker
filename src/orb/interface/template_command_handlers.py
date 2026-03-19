"""Template command handlers for CLI interface.

This module provides the interface layer handlers for template operations,
using orchestrators for architectural consistency.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Union

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.response_formatting_service import ResponseFormattingService
from orb.domain.base.exceptions import DuplicateError, EntityNotFoundError
from orb.infrastructure.di.container import get_container
from orb.infrastructure.error.decorators import handle_interface_exceptions

if TYPE_CHECKING:
    import argparse


@handle_interface_exceptions(context="list_templates", interface_type="cli")
async def handle_list_templates(
    args: "argparse.Namespace",
) -> "Union[dict[str, Any], InterfaceResponse]":
    """Handle list templates operations using the ListTemplatesOrchestrator."""
    from orb.application.services.orchestration.dtos import ListTemplatesInput
    from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
    from orb.domain.base.ports.console_port import ConsolePort

    container = get_container()
    orchestrator = container.get(ListTemplatesOrchestrator)
    formatter = container.get(ResponseFormattingService)

    if hasattr(args, "input_data") and args.input_data:
        input_data = args.input_data
        provider_name = input_data.get("provider_api") or input_data.get("provider_name")
        provider_api = input_data.get("provider_api")
        active_only = input_data.get("active_only", True)
        limit = input_data.get("limit", 50)
        offset = input_data.get("offset", 0)
    else:
        provider_name = getattr(args, "provider", None) or getattr(args, "provider_name", None)
        provider_api = getattr(args, "provider_api", None)
        active_only = getattr(args, "active_only", True)
        limit = getattr(args, "limit", 50)
        offset = getattr(args, "offset", 0)

    result = await orchestrator.execute(
        ListTemplatesInput(
            active_only=active_only,
            provider_name=provider_name,
            provider_api=provider_api,
            limit=limit,
            offset=offset,
        )
    )

    if not result.templates:
        from orb.cli.help_utils import print_getting_started_help

        console = container.get(ConsolePort)
        console.info("")
        console.info("No templates found.")
        console.info("")
        print_getting_started_help()

    return formatter.format_template_list(result.templates)


@handle_interface_exceptions(context="get_template", interface_type="cli")
async def handle_get_template(
    args: "argparse.Namespace",
) -> "Union[dict[str, Any], InterfaceResponse]":
    """Handle get template operations using the GetTemplateOrchestrator."""
    from orb.application.ports.scheduler_port import SchedulerPort
    from orb.application.services.orchestration.dtos import GetTemplateInput
    from orb.application.services.orchestration.get_template import GetTemplateOrchestrator

    template_id = getattr(args, "template_id", None) or getattr(args, "flag_template_id", None)
    if not template_id:
        return InterfaceResponse(
            data={"success": False, "error": "Template ID is required", "template": None},
            exit_code=1,
        )

    container = get_container()
    orchestrator = container.get(GetTemplateOrchestrator)
    scheduler = container.get(SchedulerPort)
    formatter = container.get(ResponseFormattingService)

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

    raw = scheduler.format_template_for_display(result.template)
    return formatter.format_config(raw)


@handle_interface_exceptions(context="create_template", interface_type="cli")
async def handle_create_template(
    args: "argparse.Namespace",
) -> "Union[dict[str, Any], InterfaceResponse]":
    """Handle create template operations using the CreateTemplateOrchestrator."""
    from orb.application.services.orchestration.create_template import CreateTemplateOrchestrator
    from orb.application.services.orchestration.dtos import CreateTemplateInput
    from orb.infrastructure.mocking.dry_run_context import is_dry_run_active

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
            data={"success": False, "error": f"Template file not found: {args.file}"}, exit_code=1
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

    image_id = template_config.get("image_id") or template_config.get("imageId")
    if not image_id:
        return InterfaceResponse(
            data={"success": False, "error": "image_id is required in template file"}, exit_code=1
        )

    if getattr(args, "validate_only", False):
        return {
            "success": True,
            "message": f"Template {template_id} is valid (not created)",
            "template_id": template_id,
            "validate_only": True,
        }

    container = get_container()
    orchestrator = container.get(CreateTemplateOrchestrator)
    formatter = container.get(ResponseFormattingService)

    try:
        result = await orchestrator.execute(
            CreateTemplateInput(
                template_id=template_id,
                provider_api=provider_api,
                image_id=image_id,
                name=template_config.get("name"),
                description=template_config.get("description"),
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

    return formatter.format_template_mutation(
        {
            "template_id": result.template_id,
            "status": "created" if result.created else "validation_failed",
            "created": result.created,
            "validation_errors": result.validation_errors,
        }
    )


@handle_interface_exceptions(context="update_template", interface_type="cli")
async def handle_update_template(
    args: "argparse.Namespace",
) -> "Union[dict[str, Any], InterfaceResponse]":
    """Handle update template operations using the UpdateTemplateOrchestrator."""
    from orb.application.services.orchestration.dtos import UpdateTemplateInput
    from orb.application.services.orchestration.update_template import UpdateTemplateOrchestrator
    from orb.infrastructure.mocking.dry_run_context import is_dry_run_active

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
            data={"success": False, "error": f"Template file not found: {file_path}"}, exit_code=1
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

    container = get_container()
    orchestrator = container.get(UpdateTemplateOrchestrator)
    formatter = container.get(ResponseFormattingService)

    try:
        result = await orchestrator.execute(
            UpdateTemplateInput(
                template_id=resolved_template_id,
                name=template_config.get("name"),
                description=template_config.get("description"),
                instance_type=template_config.get("instance_type"),
                image_id=template_config.get("image_id"),
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

    return formatter.format_template_mutation(
        {
            "template_id": result.template_id,
            "status": "updated" if result.updated else "validation_failed",
            "updated": result.updated,
            "validation_errors": result.validation_errors,
        }
    )


@handle_interface_exceptions(context="delete_template", interface_type="cli")
async def handle_delete_template(
    args: "argparse.Namespace",
) -> "Union[dict[str, Any], InterfaceResponse]":
    """Handle delete template operations using the DeleteTemplateOrchestrator."""
    from orb.application.services.orchestration.delete_template import DeleteTemplateOrchestrator
    from orb.application.services.orchestration.dtos import DeleteTemplateInput
    from orb.infrastructure.mocking.dry_run_context import is_dry_run_active

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

    container = get_container()
    formatter = container.get(ResponseFormattingService)

    if not getattr(args, "force", False):
        return formatter.format_error(
            "Destructive operation requires --force flag. Use --force to confirm deletion."
        )

    orchestrator = container.get(DeleteTemplateOrchestrator)

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

    return formatter.format_template_mutation(
        {
            "template_id": result.template_id,
            "status": "deleted" if result.deleted else "not_found",
            "deleted": result.deleted,
        }
    )


@handle_interface_exceptions(context="validate_template", interface_type="cli")
async def handle_validate_template(
    args: "argparse.Namespace",
) -> "Union[dict[str, Any], InterfaceResponse]":
    """Handle validate template operations using the ValidateTemplateOrchestrator."""
    from orb.application.services.orchestration.dtos import ValidateTemplateInput
    from orb.application.services.orchestration.validate_template import (
        ValidateTemplateOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(ValidateTemplateOrchestrator)
    formatter = container.get(ResponseFormattingService)

    if hasattr(args, "all") and args.all:
        from orb.application.services.orchestration.dtos import ListTemplatesInput
        from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator

        list_orchestrator = container.get(ListTemplatesOrchestrator)
        list_result = await list_orchestrator.execute(ListTemplatesInput())

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
        return formatter.format_template_mutation(
            {
                "template_id": result.template_id,
                "status": "validated",
                "valid": result.valid,
                "validation_errors": result.errors,
                "message": result.message,
            }
        )

    template_id = getattr(args, "template_id", None) or getattr(args, "flag_template_id", None)
    if template_id:
        try:
            result = await orchestrator.execute(ValidateTemplateInput(template_id=template_id))
        except EntityNotFoundError:
            return formatter.format_error(f"Template '{template_id}' not found")
        return formatter.format_template_mutation(
            {
                "template_id": result.template_id,
                "status": "validated",
                "valid": result.valid,
                "validation_errors": result.errors,
                "message": result.message,
            }
        )

    return InterfaceResponse(
        data={
            "success": False,
            "error": "Must provide either template_id, --file, or --all",
            "valid": False,
        },
        exit_code=1,
    )


@handle_interface_exceptions(context="refresh_templates", interface_type="cli")
async def handle_refresh_templates(
    args: "argparse.Namespace",
) -> "Union[dict[str, Any], InterfaceResponse]":
    """Handle refresh templates operations using the RefreshTemplatesOrchestrator."""
    from orb.application.services.orchestration.dtos import RefreshTemplatesInput
    from orb.application.services.orchestration.refresh_templates import (
        RefreshTemplatesOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(RefreshTemplatesOrchestrator)
    formatter = container.get(ResponseFormattingService)

    provider_name = (
        getattr(args, "provider", None)
        or getattr(args, "provider_name", None)
        or getattr(args, "provider_api", None)
    )
    result = await orchestrator.execute(RefreshTemplatesInput(provider_name=provider_name))

    return formatter.format_template_list(result.templates)
