"""
CLI command routing module.

Handles routing of parsed CLI arguments to the appropriate
command or query handlers via the flat registry.
"""

import json
from typing import Union

from domain.base.exceptions import DomainException


async def execute_command(args, app, resource_parsers) -> Union[str, tuple[str, int]]:
    """Execute command using flat registry dispatch."""
    from cli.registry import build_registry, lookup
    from cli.response_formatter import create_cli_formatter
    from domain.base.ports.scheduler_port import SchedulerPort
    from infrastructure.di.container import get_container

    build_registry()

    # Parse -f/--file and -d/--data into args.input_data
    if not hasattr(args, "input_data") or args.input_data is None:
        file_path = getattr(args, "file", None) or getattr(args, "hf_file", None)
        data_str = getattr(args, "data", None) or getattr(args, "hf_data", None)
        if file_path:
            try:
                with open(file_path) as f:
                    args.input_data = json.load(f)
            except Exception as e:
                raise DomainException(f"Failed to load input file: {e}")
        elif data_str:
            try:
                args.input_data = json.loads(data_str)
            except Exception as e:
                raise DomainException(f"Failed to parse input data: {e}")
        else:
            args.input_data = None

    resource = getattr(args, "resource", "")
    action = getattr(args, "action", "")

    handler = lookup(resource, action)
    if handler is None:
        raise ValueError(f"Unknown command: {resource} {action}")

    result = await handler(args)

    container = get_container()
    scheduler_port = container.get(SchedulerPort)
    formatter = create_cli_formatter(scheduler_port)
    return formatter.format_response(result, args)
