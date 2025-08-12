"""
CLI-specific formatting functions for human-readable output.

This module handles presentation formatting for the CLI, including:
- Rich Unicode tables with colors and borders
- ASCII table fallbacks
- List formatting for detailed views
- Field mapping for CLI display
"""

from typing import Any, Dict, List


def format_output(data: Any, format_type: str) -> str:
    """Format data according to the specified format type."""
    if format_type == "json":
        import json

        return json.dumps(data, indent=2, default=str)
    elif format_type == "yaml":
        import yaml

        return yaml.dump(data, default_flow_style=False, default_style=None)
    elif format_type == "table":
        return format_table_output(data)
    elif format_type == "list":
        return format_list_output(data)
    else:
        # Default to JSON
        import json

        return json.dumps(data, indent=2, default=str)


def format_table_output(data: Any) -> str:
    """Format data as a table."""
    if isinstance(data, dict) and "templates" in data:
        return format_templates_table(data["templates"])
    elif isinstance(data, dict) and "requests" in data:
        return format_requests_table(data["requests"])
    elif isinstance(data, dict) and "machines" in data:
        return format_machines_table(data["machines"])
    else:
        # Fallback to JSON for unknown data structures
        import json

        return json.dumps(data, indent=2, default=str)


def format_list_output(data: Any) -> str:
    """Format data as a detailed list."""
    if isinstance(data, dict) and "templates" in data:
        return format_templates_list(data["templates"])
    elif isinstance(data, dict) and "requests" in data:
        return format_requests_list(data["requests"])
    elif isinstance(data, dict) and "machines" in data:
        return format_machines_list(data["machines"])
    else:
        # Fallback to JSON for unknown data structures
        import json

        return json.dumps(data, indent=2, default=str)


def format_templates_table(templates: List[Dict]) -> str:
    """Format templates as a proper table using Rich library with Pydantic data."""
    if not templates:
        return "No templates found."

    try:
        from rich.console import Console
        from rich.table import Table

        # Create Rich table
        table = Table(show_header=True, header_style="bold magenta", show_lines=True)
        table.add_column("ID", style="cyan", width=15)
        table.add_column("Name", style="green", width=15)
        table.add_column("Provider", style="blue", width=10)
        table.add_column("CPUs", style="yellow", justify="right", width=6)
        table.add_column("RAM (MB)", style="yellow", justify="right", width=10)
        table.add_column("Max Inst", style="red", justify="right", width=8)

        # Add rows - use direct field access since Pydantic provides consistent
        # field names
        for template in templates:
            # Handle both snake_case and camelCase field names (from Pydantic aliases)
            template_id = template.get("template_id") or template.get("templateId", "N/A")
            name = template.get("name", "N/A")
            provider_api = template.get("provider_api") or template.get("providerApi", "N/A")
            max_instances = template.get("max_instances") or template.get("maxNumber", "N/A")

            # Handle different formats
            attributes = template.get("attributes")
            if attributes and isinstance(attributes, dict):
                # HF format - extract from attributes
                cpus = (
                    attributes.get("ncpus", ["Numeric", "N/A"])[1]
                    if attributes.get("ncpus")
                    else "N/A"
                )
                ram = (
                    attributes.get("nram", ["Numeric", "N/A"])[1]
                    if attributes.get("nram")
                    else "N/A"
                )
            else:
                # Standard format - derive from instance_type
                instance_type = template.get("instance_type") or template.get("instanceType", "N/A")
                cpus, ram = _derive_cpu_ram_from_instance_type(instance_type)

            # Truncate long values for table display
            table.add_row(
                str(template_id)[:15],
                str(name)[:15] if name != "N/A" else str(name),
                str(provider_api)[:10] if provider_api != "N/A" else str(provider_api),
                str(cpus),
                str(ram),
                str(max_instances),
            )

        # Capture Rich output as string
        console = Console(width=120, legacy_windows=False, force_terminal=False)
        with console.capture() as capture:
            console.print(table)

        return capture.get()

    except ImportError:
        # Fallback to ASCII table if Rich is not available
        return _format_ascii_table(templates)


def _format_ascii_table(templates: List[Dict]) -> str:
    """Fallback ASCII table formatter when Rich is not available."""

    # Define table headers
    headers = ["ID", "Name", "Provider", "CPUs", "RAM (MB)", "Max Inst"]

    # Extract data for each template using direct field access
    rows = []
    for template in templates:
        template_id = template.get("template_id") or template.get("templateId", "N/A")
        name = template.get("name", "N/A")
        provider_api = template.get("provider_api") or template.get("providerApi", "N/A")
        max_instances = template.get("max_instances") or template.get("maxNumber", "N/A")

        # Handle different formats
        attributes = template.get("attributes")
        if attributes and isinstance(attributes, dict):
            # HF format - extract from attributes
            cpus = (
                attributes.get("ncpus", ["Numeric", "N/A"])[1] if attributes.get("ncpus") else "N/A"
            )
            ram = attributes.get("nram", ["Numeric", "N/A"])[1] if attributes.get("nram") else "N/A"
        else:
            # Standard format - derive from instance_type
            instance_type = template.get("instance_type") or template.get("instanceType", "N/A")
            cpus, ram = _derive_cpu_ram_from_instance_type(instance_type)

        # Truncate long values for table display
        row = [
            template_id[:15],
            name[:15] if name != "N/A" else name,
            provider_api[:10] if provider_api != "N/A" else provider_api,
            cpus,
            ram,
            max_instances,
        ]
        rows.append(row)

    return _format_table_with_headers(headers, rows)


def _derive_cpu_ram_from_instance_type(instance_type: str) -> tuple[str, str]:
    """Derive CPU and RAM from instance type for table display."""
    if instance_type == "N/A":
        return "N/A", "N/A"

    # Simple mapping for common instance types
    cpu_ram_mapping = {
        "t2.micro": ("1", "1024"),
        "t2.small": ("1", "2048"),
        "t2.medium": ("2", "4096"),
        "t2.large": ("2", "8192"),
        "t2.xlarge": ("4", "16384"),
        "t3.micro": ("2", "1024"),
        "t3.small": ("2", "2048"),
        "t3.medium": ("2", "4096"),
        "t3.large": ("2", "8192"),
        "t3.xlarge": ("4", "16384"),
        "m5.large": ("2", "8192"),
        "m5.xlarge": ("4", "16384"),
        "m5.2xlarge": ("8", "32768"),
        "c5.large": ("2", "4096"),
        "c5.xlarge": ("4", "8192"),
        "r5.large": ("2", "16384"),
        "r5.xlarge": ("4", "32768"),
    }

    return cpu_ram_mapping.get(instance_type, ("1", "1024"))


def format_templates_list(templates: List[Dict]) -> str:
    """Format templates as a detailed list using Pydantic data."""
    if not templates:
        return "No templates found."

    lines = []

    for i, template in enumerate(templates):
        if i > 0:
            lines.append("")  # Blank line between templates

        # Use direct field access since Pydantic provides consistent field names
        template_id = template.get("template_id") or template.get("templateId", "N/A")
        name = template.get("name", "N/A")
        provider_api = template.get("provider_api") or template.get("providerApi", "N/A")
        instance_type = template.get("instance_type") or template.get("instanceType", "N/A")
        max_instances = template.get("max_instances") or template.get("maxNumber", "N/A")

        lines.append(f"Template: {template_id}")
        lines.append(f"  Name: {name}")
        lines.append(f"  Provider: {provider_api}")
        lines.append(f"  Instance Type: {instance_type}")
        lines.append(f"  Max Instances: {max_instances}")

        # Handle HF format attributes
        attributes = template.get("attributes")
        if attributes and isinstance(attributes, dict):
            # Extract info from HF attributes format
            cpus = (
                attributes.get("ncpus", ["Numeric", "N/A"])[1] if attributes.get("ncpus") else "N/A"
            )
            ram = attributes.get("nram", ["Numeric", "N/A"])[1] if attributes.get("nram") else "N/A"
            lines.append(f"  CPUs: {cpus}")
            lines.append(f"  RAM (MB): {ram}")

        # Add other fields if available
        description = template.get("description", "N/A")
        if description != "N/A":
            lines.append(f"  Description: {description}")

        image_id = template.get("image_id") or template.get("imageId", "N/A")
        if image_id != "N/A":
            lines.append(f"  Image ID: {image_id}")

        subnet_ids = template.get("subnet_ids") or template.get("subnetIds", "N/A")
        if subnet_ids != "N/A":
            lines.append(f"  Subnet IDs: {subnet_ids}")

    return "\n".join(lines)


def format_requests_list(requests: List[Dict]) -> str:
    """Format requests as a detailed list using Pydantic data."""
    if not requests:
        return "No requests found."

    lines = []

    for i, request in enumerate(requests):
        if i > 0:
            lines.append("")  # Blank line between requests

        # Use direct field access with both snake_case and camelCase support
        request_id = request.get("request_id") or request.get("requestId", "N/A")
        status = request.get("status", "N/A")
        template_id = request.get("template_id") or request.get("templateId", "N/A")
        num_requested = request.get("requested_count") or request.get("numRequested", "N/A")
        created_at = request.get("created_at") or request.get("createdAt", "N/A")

        lines.append(f"Request: {request_id}")
        lines.append(f"  Status: {status}")
        lines.append(f"  Template: {template_id}")
        lines.append(f"  Requested: {num_requested}")
        lines.append(f"  Created: {created_at}")

    return "\n".join(lines)


def format_machines_list(machines: List[Dict]) -> str:
    """Format machines as a detailed list using Pydantic data."""
    if not machines:
        return "No machines found."

    lines = []

    for i, machine in enumerate(machines):
        if i > 0:
            lines.append("")  # Blank line between machines

        # Use direct field access with both snake_case and camelCase support
        machine_id = (
            machine.get("instance_id")
            or machine.get("instanceId")
            or machine.get("machine_id")
            or machine.get("machineId", "N/A")
        )
        name = (
            machine.get("name") or machine.get("machine_name") or machine.get("machineName", "N/A")
        )
        status = machine.get("status") or machine.get("state", "N/A")
        instance_type = (
            machine.get("instance_type")
            or machine.get("instanceType")
            or machine.get("vm_type")
            or machine.get("vmType", "N/A")
        )
        private_ip = (
            machine.get("private_ip")
            or machine.get("privateIp")
            or machine.get("private_ip_address", "N/A")
        )

        lines.append(f"Machine: {machine_id}")
        lines.append(f"  Name: {name}")
        lines.append(f"  Status: {status}")
        lines.append(f"  Instance Type: {instance_type}")
        lines.append(f"  Private IP: {private_ip}")

    return "\n".join(lines)


def format_machines_table(machines: List[Dict]) -> str:
    """Format machines as a table using Pydantic data."""
    if not machines:
        return "No machines found."

    # Define table headers
    headers = ["ID", "Name", "Status", "Type", "Private IP"]

    # Extract data for each machine using direct field access
    rows = []
    for machine in machines:
        # Support both snake_case and camelCase field names
        machine_id = (
            machine.get("instance_id")
            or machine.get("instanceId")
            or machine.get("machine_id")
            or machine.get("machineId", "N/A")
        )
        name = (
            machine.get("name") or machine.get("machine_name") or machine.get("machineName", "N/A")
        )
        status = machine.get("status") or machine.get("state", "N/A")
        instance_type = (
            machine.get("instance_type")
            or machine.get("instanceType")
            or machine.get("vm_type")
            or machine.get("vmType", "N/A")
        )
        private_ip = (
            machine.get("private_ip")
            or machine.get("privateIp")
            or machine.get("private_ip_address", "N/A")
        )

        # Truncate long values for table display
        row = [
            str(machine_id)[:15],
            str(name)[:15] if name != "N/A" else str(name),
            str(status)[:10],
            str(instance_type)[:10],
            str(private_ip),
        ]
        rows.append(row)

    return _format_table_with_headers(headers, rows)


def format_requests_table(requests: List[Dict]) -> str:
    """Format requests as a table using Pydantic data."""
    if not requests:
        return "No requests found."

    # Define table headers
    headers = ["ID", "Status", "Template", "Requested", "Created"]

    # Extract data for each request using direct field access
    rows = []
    for request in requests:
        # Support both snake_case and camelCase field names
        request_id = request.get("request_id") or request.get("requestId", "N/A")
        status = request.get("status", "N/A")
        template_id = request.get("template_id") or request.get("templateId", "N/A")
        num_requested = request.get("requested_count") or request.get("numRequested", "N/A")
        created_at = request.get("created_at") or request.get("createdAt", "N/A")

        # Truncate long values for table display
        row = [
            str(request_id)[:15],
            str(status)[:10],
            str(template_id)[:15],
            str(num_requested),
            (
                str(created_at)[:19] if created_at != "N/A" else str(created_at)
            ),  # Show date/time only
        ]
        rows.append(row)

    return _format_table_with_headers(headers, rows)


def _format_table_with_headers(headers: List[str], rows: List[List[str]]) -> str:
    """Format data as ASCII table with headers."""
    if not rows:
        return "No data to display."

    # Calculate column widths
    all_rows = [headers] + rows
    widths = [max(len(str(row[i])) for row in all_rows) for i in range(len(headers))]

    # Format table
    def format_row(row, widths):
        """Format a single table row with proper column alignment."""
        return "| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |"

    def format_separator(widths):
        """Generate table separator line with proper column widths."""
        return "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    lines = []
    lines.append(format_separator(widths))
    lines.append(format_row(headers, widths))
    lines.append(format_separator(widths))
    for row in rows:
        lines.append(format_row(row, widths))
    lines.append(format_separator(widths))

    return "\n".join(lines)
