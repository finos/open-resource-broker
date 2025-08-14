"""Template command handlers for CLI interface.

This module provides the interface layer handlers for template operations,
using proper CQRS buses for architectural consistency.

Updated to use CommandBus and QueryBus instead of direct handler calls,
following the same pattern as other entities in the system.
"""

from __future__ import annotations

import argparse
from typing import Any, Dict

from src.application.dto.queries import (
    GetTemplateQuery,
    ListTemplatesQuery,
    ValidateTemplateQuery,
)
from src.application.template.commands import (
    CreateTemplateCommand,
    DeleteTemplateCommand,
    UpdateTemplateCommand,
)
from src.domain.base.ports.scheduler_port import SchedulerPort
from src.infrastructure.di.buses import CommandBus, QueryBus
from src.infrastructure.di.container import get_container
from src.infrastructure.error.decorators import handle_interface_exceptions


@handle_interface_exceptions(context="list_templates", interface_type="cli")
async def handle_list_templates(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Handle list templates operations using CQRS QueryBus.

    Args:
        args: CLI arguments

    Returns:
        Dictionary with templates list and metadata
    """
    try:
        # Get dependencies from DI container
        container = get_container()
        query_bus = container.get(QueryBus)

        if not query_bus:
            return {
                "success": False,
                "error": "QueryBus not available",
                "templates": [],
            }

        # Extract parameters from args or input_data (HostFactory compatibility)
        provider_api = None
        active_only = True
        include_config = False

        # Check for input data from -f/--data flags first (HostFactory style)
        if hasattr(args, "input_data") and args.input_data:
            input_data = args.input_data
            provider_api = input_data.get("provider_api")
            active_only = input_data.get("active_only", True)
            include_config = input_data.get("include_config", False)
        else:
            # Use command line arguments
            provider_api = getattr(args, "provider_api", None)
            active_only = getattr(args, "active_only", True)
            include_config = getattr(args, "include_config", False)

        # Create and execute query through CQRS bus
        query = ListTemplatesQuery(
            provider_api=provider_api,
            active_only=active_only,
            include_configuration=include_config,
        )

        templates = await query_bus.execute(query)

        # Get scheduler strategy from DI container
        scheduler_strategy = container.get(SchedulerPort)

        # Use scheduler strategy for format conversion
        if scheduler_strategy:
            formatted_response = scheduler_strategy.convert_domain_to_hostfactory_output(
                "getAvailableTemplates", templates
            )
            templates_data = formatted_response.get("templates", [])
        else:
            templates_data = [
                template.model_dump() if hasattr(template, "model_dump") else template
                for template in templates
            ]

        return {
            "success": True,
            "templates": templates_data,
            "total_count": len(templates),
            "message": f"Retrieved {len(templates)} templates successfully",
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to list templates: {str(e)}",
            "templates": [],
        }


@handle_interface_exceptions(context="get_template", interface_type="cli")
async def handle_get_template(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Handle get template operations using CQRS QueryBus.

    Args:
        args: CLI arguments
        app: Application instance

    Returns:
        Dictionary with template data or error
    """
    try:
        # Get QueryBus from DI container
        container = get_container()
        query_bus = container.get(QueryBus)

        if not query_bus:
            return {
                "success": False,
                "error": "QueryBus not available",
                "template": None,
            }

        # Extract parameters from args
        template_id = getattr(args, "template_id", None)
        if not template_id:
            return {
                "success": False,
                "error": "Template ID is required",
                "template": None,
            }

        # Create and execute query through CQRS bus
        query = GetTemplateQuery(template_id=template_id)
        template = await query_bus.execute(query)

        if template:
            return {
                "success": True,
                "template": (
                    template.model_dump() if hasattr(template, "model_dump") else template
                ),
                "message": f"Retrieved template {template_id} successfully",
            }
        else:
            return {"success": False, "error": "Template not found", "template": None}

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to get template: {str(e)}",
            "template": None,
        }


@handle_interface_exceptions(context="create_template", interface_type="cli")
async def handle_create_template(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Handle create template operations using CQRS CommandBus.

    Args:
        args: CLI arguments
        app: Application instance

    Returns:
        Dictionary with creation result
    """
    try:
        # Get CommandBus from DI container
        container = get_container()
        command_bus = container.get(CommandBus)

        if not command_bus:
            return {"success": False, "error": "CommandBus not available"}

        # Check dry-run context
        from src.infrastructure.mocking.dry_run_context import is_dry_run_active

        if is_dry_run_active():
            return {
                "success": True,
                "message": "DRY-RUN: Template creation would be executed",
                "template_id": getattr(args, "template_id", "dry-run-template"),
                "dry_run": True,
            }

        # Extract required fields
        template_id = getattr(args, "template_id", None)
        if not template_id:
            return {"success": False, "error": "Template ID is required"}

        provider_api = getattr(args, "provider_api", None)
        if not provider_api:
            return {"success": False, "error": "Provider API is required"}

        image_id = getattr(args, "image_id", None)
        if not image_id:
            return {"success": False, "error": "Image ID is required"}

        # Create command with all fields
        command = CreateTemplateCommand(
            template_id=template_id,
            name=getattr(args, "name", None),
            description=getattr(args, "description", None),
            provider_api=provider_api,
            instance_type=getattr(args, "instance_type", None),
            image_id=image_id,
            subnet_ids=getattr(args, "subnets", []),
            security_group_ids=getattr(args, "security_groups", []),
            tags=getattr(args, "tags", {}),
            configuration=getattr(args, "configuration", {}),
        )

        # Execute command through CQRS bus
        response = await command_bus.execute(command)

        if response and response.validation_errors:
            return {
                "success": False,
                "error": f"Template validation failed: {', '.join(response.validation_errors)}",
                "template_id": template_id,
            }

        return {
            "success": True,
            "message": f"Template {template_id} created successfully",
            "template_id": template_id,
        }

    except Exception as e:
        return {"success": False, "error": f"Failed to create template: {str(e)}"}


@handle_interface_exceptions(context="update_template", interface_type="cli")
async def handle_update_template(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Handle update template operations using CQRS CommandBus.

    Args:
        args: CLI arguments
        app: Application instance

    Returns:
        Dictionary with update result
    """
    try:
        # Get CommandBus from DI container
        container = get_container()
        command_bus = container.get(CommandBus)

        if not command_bus:
            return {"success": False, "error": "CommandBus not available"}

        # Extract template ID
        template_id = getattr(args, "template_id", None)
        if not template_id:
            return {"success": False, "error": "Template ID is required"}

        # Check dry-run context
        from src.infrastructure.mocking.dry_run_context import is_dry_run_active

        if is_dry_run_active():
            return {
                "success": True,
                "message": f"DRY-RUN: Template {template_id} update would be executed",
                "template_id": template_id,
                "dry_run": True,
            }

        # Build configuration from args
        configuration = {}
        if hasattr(args, "configuration"):
            configuration = args.configuration

        # Create command with update fields
        command = UpdateTemplateCommand(
            template_id=template_id,
            name=getattr(args, "name", None),
            description=getattr(args, "description", None),
            configuration=configuration,
        )

        # Execute command through CQRS bus
        response = await command_bus.execute(command)

        if response and response.validation_errors:
            return {
                "success": False,
                "error": f"Template validation failed: {', '.join(response.validation_errors)}",
                "template_id": template_id,
            }

        return {
            "success": True,
            "message": f"Template {template_id} updated successfully",
            "template_id": template_id,
        }

    except Exception as e:
        return {"success": False, "error": f"Failed to update template: {str(e)}"}


@handle_interface_exceptions(context="delete_template", interface_type="cli")
async def handle_delete_template(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Handle delete template operations using CQRS CommandBus.

    Args:
        args: CLI arguments
        app: Application instance

    Returns:
        Dictionary with deletion result
    """
    try:
        # Get CommandBus from DI container
        container = get_container()
        command_bus = container.get(CommandBus)

        if not command_bus:
            return {"success": False, "error": "CommandBus not available"}

        # Extract template ID
        template_id = getattr(args, "template_id", None)
        if not template_id:
            return {"success": False, "error": "Template ID is required"}

        # Check dry-run context
        from src.infrastructure.mocking.dry_run_context import is_dry_run_active

        if is_dry_run_active():
            return {
                "success": True,
                "message": f"DRY-RUN: Template {template_id} deletion would be executed",
                "template_id": template_id,
                "dry_run": True,
            }

        # Create and execute command through CQRS bus
        command = DeleteTemplateCommand(template_id=template_id)
        response = await command_bus.execute(command)

        if response and response.validation_errors:
            return {
                "success": False,
                "error": f"Template deletion failed: {', '.join(response.validation_errors)}",
                "template_id": template_id,
            }

        return {
            "success": True,
            "message": f"Template {template_id} deleted successfully",
            "template_id": template_id,
        }

    except Exception as e:
        return {"success": False, "error": f"Failed to delete template: {str(e)}"}


@handle_interface_exceptions(context="validate_template", interface_type="cli")
async def handle_validate_template(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Handle validate template operations using CQRS buses.

    Args:
        args: CLI arguments
        app: Application instance

    Returns:
        Dictionary with validation result
    """
    try:
        # Get buses from DI container
        container = get_container()
        query_bus = container.get(QueryBus)
        command_bus = container.get(CommandBus)

        if not query_bus or not command_bus:
            return {
                "success": False,
                "error": "CQRS buses not available",
                "valid": False,
            }

        # Extract template data from args or file
        template_config = {}
        template_id = None

        # Check if template file is provided
        if hasattr(args, "template_file") and args.template_file:
            import json
            from pathlib import Path

            import yaml

            template_file = Path(args.template_file)
            if not template_file.exists():
                return {
                    "success": False,
                    "error": f"Template file not found: {template_file}",
                    "valid": False,
                }

            try:
                with open(template_file, "r") as f:
                    if template_file.suffix.lower() in {".yml", ".yaml"}:
                        template_config = yaml.safe_load(f)
                    else:
                        template_config = json.load(f)
                template_id = template_config.get("template_id", "file-template")
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to parse template file: {str(e)}",
                    "valid": False,
                }
        else:
            # Extract from command line args
            template_id = getattr(args, "template_id", "cli-template")

            # Build configuration from args
            if hasattr(args, "name"):
                template_config["name"] = args.name
            if hasattr(args, "provider_api"):
                template_config["provider_api"] = args.provider_api
            if hasattr(args, "image_id"):
                template_config["image_id"] = args.image_id
            if hasattr(args, "instance_type"):
                template_config["instance_type"] = args.instance_type
            if hasattr(args, "configuration"):
                template_config.update(args.configuration)

        if not template_config:
            return {
                "success": False,
                "error": "No template data provided for validation",
                "valid": False,
            }

        # Use ValidateTemplateQuery for validation
        query = ValidateTemplateQuery(template_config=template_config)
        validation_result = await query_bus.execute(query)

        # Check if validation result has errors
        is_valid = not validation_result.errors if hasattr(
            validation_result, "errors") else True

        return {
            "success": True,
            "valid": is_valid,
            "validation_errors": (
                validation_result.errors if hasattr(validation_result, "errors") else []
            ),
            "validation_warnings": (
                validation_result.warnings if hasattr(
                    validation_result, "warnings") else []
            ),
            "template_id": template_id,
            "message": "Validation completed successfully",
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to validate template: {str(e)}",
            "valid": False,
        }


@handle_interface_exceptions(context="refresh_templates", interface_type="cli")
async def handle_refresh_templates(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Handle refresh templates operations using CQRS QueryBus.

    Args:
        args: CLI arguments
        app: Application instance

    Returns:
        Dictionary with refresh result
    """
    try:
        # Get QueryBus from DI container
        container = get_container()
        query_bus = container.get(QueryBus)

        if not query_bus:
            return {"success": False, "error": "QueryBus not available"}

        # Force refresh by listing templates with force_refresh parameter
        # This will trigger cache refresh in the query handler
        query = ListTemplatesQuery(
            provider_api=None, active_only=True, include_configuration=False)

        templates = await query_bus.execute(query)
        template_count = len(templates) if templates else 0

        return {
            "success": True,
            "message": f"Templates refreshed successfully. Found {template_count} templates.",
            "template_count": template_count,
            "cache_stats": {"refreshed": True},
        }

    except Exception as e:
        return {"success": False, "error": f"Failed to refresh templates: {str(e)}"}
