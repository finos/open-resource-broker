"""Template command handlers for CLI interface.

This module provides the interface layer handlers for template operations,
using orchestrators for architectural consistency.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from orb.application.ports.scheduler_port import SchedulerPort
from orb.domain.base.exceptions import DuplicateError, EntityNotFoundError
from orb.infrastructure.di.container import get_container
from orb.infrastructure.error.decorators import handle_interface_exceptions

if TYPE_CHECKING:
    import argparse


@handle_interface_exceptions(context="list_templates", interface_type="cli")
async def handle_list_templates(args: argparse.Namespace) -> dict[str, Any]:
    """Handle list templates operations using the ListTemplatesOrchestrator."""
    from orb.application.services.orchestration.dtos import ListTemplatesInput
    from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
    from orb.domain.base.ports.console_port import ConsolePort

    container = get_container()
    orchestrator = container.get(ListTemplatesOrchestrator)
    scheduler = container.get(SchedulerPort)

    # Extract parameters from args or input_data (HostFactory compatibility)
    if hasattr(args, "input_data") and args.input_data:
        input_data = args.input_data
        provider_name = input_data.get("provider_api") or input_data.get("provider_name")
        active_only = input_data.get("active_only", True)
    else:
        provider_name = getattr(args, "provider_api", None) or getattr(args, "provider_name", None)
        active_only = getattr(args, "active_only", True)

    result = await orchestrator.execute(
        ListTemplatesInput(active_only=active_only, provider_name=provider_name)
    )

    if not result.templates:
        from orb.cli.help_utils import print_getting_started_help

        console = container.get(ConsolePort)
        console.info("")
        console.info("No templates found.")
        console.info("")
        print_getting_started_help()

    return scheduler.format_templates_response(result.templates)


@handle_interface_exceptions(context="get_template", interface_type="cli")
async def handle_get_template(args: argparse.Namespace) -> dict[str, Any]:
    """Handle get template operations using the GetTemplateOrchestrator."""
    from orb.application.services.orchestration.dtos import GetTemplateInput
    from orb.application.services.orchestration.get_template import GetTemplateOrchestrator

    template_id = getattr(args, "template_id", None) or getattr(args, "flag_template_id", None)
    if not template_id:
        return {"success": False, "error": "Template ID is required", "template": None}

    container = get_container()
    orchestrator = container.get(GetTemplateOrchestrator)
    scheduler = container.get(SchedulerPort)

    result = await orchestrator.execute(GetTemplateInput(template_id=template_id))

    if not result.template:
        return {
            "success": False,
            "error": f"Template '{template_id}' not found",
            "template": None,
        }

    return scheduler.format_template_for_display(result.template)


@handle_interface_exceptions(context="create_template", interface_type="cli")
async def handle_create_template(args: argparse.Namespace) -> dict[str, Any]:
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
        return {"success": False, "error": "Template file is required"}

    try:
        with open(args.file) as f:
            template_config = json.load(f)
    except FileNotFoundError:
        return {"success": False, "error": f"Template file not found: {args.file}"}
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Invalid JSON in template file: {e}"}

    template_id = template_config.get("template_id") or template_config.get("templateId")
    if not template_id:
        return {"success": False, "error": "template_id is required in template file"}

    provider_api = template_config.get("provider_api") or template_config.get("providerApi")
    if not provider_api:
        return {"success": False, "error": "provider_api is required in template file"}

    image_id = template_config.get("image_id") or template_config.get("imageId")
    if not image_id:
        return {"success": False, "error": "image_id is required in template file"}

    if getattr(args, "validate_only", False):
        return {
            "success": True,
            "message": f"Template {template_id} is valid (not created)",
            "template_id": template_id,
            "validate_only": True,
        }

    container = get_container()
    orchestrator = container.get(CreateTemplateOrchestrator)
    scheduler = container.get(SchedulerPort)

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
        return {
            "success": False,
            "error": f"Template '{template_id}' already exists",
            "template_id": template_id,
        }

    if result.validation_errors:
        return {
            "success": False,
            "error": f"Template validation failed: {', '.join(result.validation_errors)}",
            "template_id": template_id,
        }

    return scheduler.format_template_mutation_response(result.raw)


@handle_interface_exceptions(context="update_template", interface_type="cli")
async def handle_update_template(args: argparse.Namespace) -> dict[str, Any]:
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
        return {"success": False, "error": "Template file is required"}

    try:
        with open(file_path) as f:
            template_config = json.load(f)
    except FileNotFoundError:
        return {"success": False, "error": f"Template file not found: {file_path}"}
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Invalid JSON in template file: {e}"}

    if not isinstance(template_config, dict):
        return {"success": False, "error": "Template file must contain a JSON object"}

    file_template_id = template_config.get("template_id") or template_config.get("templateId")
    resolved_template_id = template_id or file_template_id
    if not resolved_template_id:
        return {"success": False, "error": "Template ID is required (via arg or file)"}

    container = get_container()
    orchestrator = container.get(UpdateTemplateOrchestrator)
    scheduler = container.get(SchedulerPort)

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
        return {
            "success": False,
            "error": f"Template '{resolved_template_id}' not found",
            "template_id": resolved_template_id,
        }

    if result.validation_errors:
        return {
            "success": False,
            "error": f"Template validation failed: {', '.join(result.validation_errors)}",
            "template_id": resolved_template_id,
        }

    return scheduler.format_template_mutation_response(result.raw)


@handle_interface_exceptions(context="delete_template", interface_type="cli")
async def handle_delete_template(args: argparse.Namespace) -> dict[str, Any]:
    """Handle delete template operations using the DeleteTemplateOrchestrator."""
    from orb.application.services.orchestration.delete_template import DeleteTemplateOrchestrator
    from orb.application.services.orchestration.dtos import DeleteTemplateInput
    from orb.infrastructure.mocking.dry_run_context import is_dry_run_active

    template_id = getattr(args, "template_id", None) or getattr(args, "flag_template_id", None)
    if not template_id:
        return {"success": False, "error": "Template ID is required"}

    if is_dry_run_active():
        return {
            "success": True,
            "message": f"DRY-RUN: Template {template_id} deletion would be executed",
            "template_id": template_id,
            "dry_run": True,
        }

    container = get_container()
    orchestrator = container.get(DeleteTemplateOrchestrator)
    scheduler = container.get(SchedulerPort)

    try:
        result = await orchestrator.execute(DeleteTemplateInput(template_id=template_id))
    except EntityNotFoundError:
        return {
            "success": False,
            "error": f"Template '{template_id}' not found",
            "template_id": template_id,
        }

    if not result.deleted:
        return {
            "success": False,
            "error": f"Template '{template_id}' could not be deleted",
            "template_id": template_id,
        }

    return scheduler.format_template_mutation_response(result.raw)


@handle_interface_exceptions(context="validate_template", interface_type="cli")
async def handle_validate_template(args: argparse.Namespace) -> dict[str, Any]:
    """Handle validate template operations using the ValidateTemplateOrchestrator."""
    from orb.application.services.orchestration.dtos import ValidateTemplateInput
    from orb.application.services.orchestration.validate_template import (
        ValidateTemplateOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(ValidateTemplateOrchestrator)
    scheduler = container.get(SchedulerPort)

    # --all: validate every loaded template
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

    # --file: validate from file
    if hasattr(args, "file") and args.file:
        from pathlib import Path

        import yaml

        template_file = Path(args.file)
        if not template_file.exists():
            return {
                "success": False,
                "error": f"Template file not found: {template_file}",
                "valid": False,
            }

        try:
            with open(template_file) as f:
                if template_file.suffix.lower() in {".yml", ".yaml"}:
                    template_config = yaml.safe_load(f)
                else:
                    template_config = json.load(f)
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to parse template file: {e!s}",
                "valid": False,
            }

        template_id = template_config.get("template_id", "file-template")
        result = await orchestrator.execute(
            ValidateTemplateInput(template_id=template_id, config=template_config)
        )
        return scheduler.format_template_mutation_response(result.raw)

    # template_id: validate a loaded template by ID
    if hasattr(args, "template_id") and args.template_id:
        template_id = args.template_id
        result = await orchestrator.execute(ValidateTemplateInput(template_id=template_id))
        return scheduler.format_template_mutation_response(result.raw)

    return {
        "success": False,
        "error": "Must provide either template_id, --file, or --all",
        "valid": False,
    }


@handle_interface_exceptions(context="refresh_templates", interface_type="cli")
async def handle_refresh_templates(args: argparse.Namespace) -> dict[str, Any]:
    """Handle refresh templates operations using the RefreshTemplatesOrchestrator."""
    from orb.application.services.orchestration.dtos import RefreshTemplatesInput
    from orb.application.services.orchestration.refresh_templates import (
        RefreshTemplatesOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(RefreshTemplatesOrchestrator)

    provider_name = getattr(args, "provider_name", None) or getattr(args, "provider_api", None)
    result = await orchestrator.execute(RefreshTemplatesInput(provider_name=provider_name))

    scheduler = container.get(SchedulerPort)
    return scheduler.format_templates_response(result.templates)
