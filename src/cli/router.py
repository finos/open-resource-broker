"""
CLI command routing module.

Handles routing of parsed CLI arguments to the appropriate
command or query handlers via the CQRS pattern.
"""

from typing import Union

from domain.base.exceptions import DomainException


async def execute_command(args, app, resource_parsers) -> Union[str, tuple[str, int]]:
    """Execute command using pure CQRS pattern."""
    # Process input data from -f/--file or -d/--data flags
    input_data = None
    if hasattr(args, "file") and args.file:
        try:
            import json

            with open(args.file) as f:
                input_data = json.load(f)
        except Exception as e:
            raise DomainException(f"Failed to load input file: {e}")
    elif hasattr(args, "data") and args.data:
        try:
            import json

            input_data = json.loads(args.data)
        except Exception as e:
            raise DomainException(f"Failed to parse input data: {e}")

    args.input_data = input_data

    # Handle special cases that return direct results
    if args.resource == "init":
        from interface.init_command_handler import handle_init

        result = await handle_init(args)

        from cli.response_formatter import create_cli_formatter

        formatter = create_cli_formatter()
        return formatter.format_response(result, args)

    if args.resource == "mcp" and args.action == "serve":
        from interface.mcp.server.handler import handle_mcp_serve

        result = await handle_mcp_serve(args)

        from cli.response_formatter import create_cli_formatter

        formatter = create_cli_formatter()
        return formatter.format_response(result, args)

    if args.resource == "mcp" and args.action == "tools":
        from interface.mcp_command_handlers import (
            handle_mcp_tools_call,
            handle_mcp_tools_info,
            handle_mcp_tools_list,
        )

        tools_action = getattr(args, "tools_action", None)
        if tools_action == "list":
            result = await handle_mcp_tools_list(args)
        elif tools_action == "call":
            result = await handle_mcp_tools_call(args)
        elif tools_action == "info":
            result = await handle_mcp_tools_info(args)
        else:
            raise ValueError(f"Unknown MCP tools action: {tools_action}")

        from cli.response_formatter import create_cli_formatter

        formatter = create_cli_formatter()
        return formatter.format_response(result, args)

    # Use pure CQRS pattern for all other commands
    from application.interfaces.command_query import Command
    from cli.command_factory import cli_command_factory
    from cli.response_formatter import create_cli_formatter
    from domain.base.ports.scheduler_port import SchedulerPort
    from infrastructure.di.buses import CommandBus, QueryBus
    from infrastructure.di.container import get_container

    container = get_container()
    command_bus = container.get(CommandBus)
    query_bus = container.get(QueryBus)
    scheduler_port = container.get(SchedulerPort)

    # Handle infrastructure commands directly (not through CQRS)
    if hasattr(args, "resource") and args.resource in ["infrastructure", "infra"]:
        from interface.infrastructure_command_handler import (
            handle_infrastructure_discover,
            handle_infrastructure_show,
            handle_infrastructure_validate,
        )

        if args.action == "discover":
            result = await handle_infrastructure_discover(args)
        elif args.action == "show":
            result = await handle_infrastructure_show(args)
        elif args.action == "validate":
            result = await handle_infrastructure_validate(args)
        else:
            raise ValueError(f"Unknown infrastructure action: {args.action}")

    # Handle provider configuration commands directly
    elif (
        hasattr(args, "resource")
        and args.resource in ["providers", "provider"]
        and args.action in ["add", "remove", "update", "set-default", "get-default", "show"]
    ):
        from interface.provider_config_handler import (
            handle_provider_add,
            handle_provider_get_default,
            handle_provider_remove,
            handle_provider_set_default,
            handle_provider_show,
            handle_provider_update,
        )

        if args.action == "add":
            result = await handle_provider_add(args)
        elif args.action == "remove":
            result = await handle_provider_remove(args)
        elif args.action == "update":
            result = await handle_provider_update(args)
        elif args.action == "set-default":
            result = await handle_provider_set_default(args)
        elif args.action == "get-default":
            result = await handle_provider_get_default(args)
        elif args.action == "show":
            result = await handle_provider_show(args)
        else:
            raise ValueError(f"Unknown provider config action: {args.action}")

    # Handle system commands directly
    elif hasattr(args, "resource") and args.resource == "system":
        if args.action == "serve":
            from interface.serve_command_handler import handle_serve_api

            result = await handle_serve_api(args)
        elif args.action == "status":
            from interface.system_command_handlers import handle_system_status

            result = await handle_system_status(args)
        elif args.action == "health":
            from interface.system_command_handlers import handle_system_health

            result = await handle_system_health(args)
        elif args.action == "metrics":
            from interface.system_command_handlers import handle_system_metrics

            result = await handle_system_metrics(args)
        else:
            raise ValueError(f"Unknown system action: {args.action}")
    else:
        # Validate show commands before creating command/query
        if hasattr(args, "resource") and hasattr(args, "action") and args.action == "show":
            if getattr(args, "all", False):
                resource_name = args.resource
                if resource_name in ["templates", "template"]:
                    raise DomainException(
                        "The --all flag is not supported with 'show' commands. "
                        "Use 'orb templates list' to see multiple templates."
                    )
                elif resource_name in ["machines", "machine"]:
                    raise DomainException(
                        "The --all flag is not supported with 'show' commands. "
                        "Use 'orb machines list' to see multiple machines."
                    )
                elif resource_name in ["requests", "request"]:
                    raise DomainException(
                        "The --all flag is not supported with 'show' commands. "
                        "Use 'orb requests list' to see multiple requests."
                    )

            resource_name = args.resource
            if resource_name in ["templates", "template"]:
                template_id = getattr(args, "template_id", None) or getattr(args, "flag_template_id", None)
                if not template_id:
                    raise DomainException(
                        "Template ID is required for 'show' command. "
                        "Usage: orb templates show <template-id> or orb templates show --template-id <template-id>"
                    )
            elif resource_name in ["machines", "machine"]:
                machine_id = getattr(args, "machine_id", None) or getattr(args, "flag_machine_id", None)
                if not machine_id:
                    raise DomainException(
                        "Machine ID is required for 'show' command. "
                        "Usage: orb machines show <machine-id> or orb machines show --machine-id <machine-id>"
                    )
            elif resource_name in ["requests", "request"]:
                request_id = getattr(args, "request_id", None)
                if not request_id:
                    raise DomainException(
                        "Request ID required. Use 'orb requests list' for multiple requests"
                    )

        # Create command or query from CLI args
        command_or_query = cli_command_factory.create_command_or_query(args)

        # Handle special cases where command factory returns None (non-CQRS commands)
        if command_or_query is None:
            if (
                hasattr(args, "resource")
                and args.resource in ["machines", "machine"]
                and args.action == "status"
            ):
                from interface.machine_command_handlers import handle_get_machine_status

                result = await handle_get_machine_status(args)
            elif (
                hasattr(args, "resource")
                and args.resource in ["templates", "template"]
                and args.action == "validate"
            ):
                from interface.template_command_handlers import handle_validate_template

                result = await handle_validate_template(args)
            elif (
                hasattr(args, "resource")
                and args.resource in ["machines", "machine"]
                and args.action in ["return", "terminate"]
            ):
                from interface.request_command_handlers import handle_request_return_machines

                result = await handle_request_return_machines(args)
            elif (
                hasattr(args, "resource")
                and args.resource in ["machines", "machine"]
                and args.action == "stop"
            ):
                from interface.machine_command_handlers import handle_stop_machines

                result = await handle_stop_machines(args)
            elif (
                hasattr(args, "resource")
                and args.resource in ["machines", "machine"]
                and args.action == "start"
            ):
                from interface.machine_command_handlers import handle_start_machines

                result = await handle_start_machines(args)
            elif (
                hasattr(args, "resource")
                and args.resource == "requests"
                and args.action == "status"
            ):
                from interface.request_command_handlers import handle_get_request_status

                result = await handle_get_request_status(args)
            elif (
                hasattr(args, "resource")
                and args.resource in ["machines", "machine"]
                and args.action == "list"
            ):
                from interface.machine_command_handlers import handle_list_machines

                result = await handle_list_machines(args)
            else:
                raise ValueError(f"Unknown command: {args.resource} {args.action}")
        else:
            from application.dto.base import BaseCommand

            if isinstance(command_or_query, (Command, BaseCommand)):
                result = await command_bus.execute(command_or_query)  # type: ignore[arg-type]
            else:
                result = await query_bus.execute(command_or_query)

    # Format response for CLI output
    formatter = create_cli_formatter(scheduler_port)
    return formatter.format_response(result, args)
