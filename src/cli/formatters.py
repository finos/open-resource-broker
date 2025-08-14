"""
CLI-specific formatting functions for human-readable output.

This module handles presentation formatting for the CLI, including:
- Rich Unicode tables with colors and borders
- ASCII table fallbacks
- List formatting for detailed views
- Pure dynamic field handling - no hardcoded field mappings
"""

from typing import Any, Dict, List


def format_output(data: Any, format_type: str) -> str:
    """Format data according to the specified format type."""
    if format_type == "json":
        import json
        return json.dumps(data, indent=2, default=str)
    elif format_type == "yaml":
        try:
            import yaml
            return yaml.dump(data, default_flow_style=False, default_style=None)
        except ImportError:
            import json
            return json.dumps(data, indent=2, default=str)
    elif format_type == "table":
        return format_table_output(data)
    elif format_type == "list":
        return format_list_output(data)
    else:
        import json
        return json.dumps(data, indent=2, default=str)


def format_table_output(data: Any) -> str:
    """Format data as a table."""
    if isinstance(data, dict):
        for key, items in data.items():
            if isinstance(items, list) and items:
                return format_generic_table(items, key.title())
    # Fallback to JSON for unknown data structures
    import json
    return json.dumps(data, indent=2, default=str)


def format_list_output(data: Any) -> str:
    """Format data as a detailed list."""
    if isinstance(data, dict):
        for key, items in data.items():
            if isinstance(items, list) and items:
                return format_generic_list(items, key.title())
    # Fallback to JSON for unknown data structures
    import json
    return json.dumps(data, indent=2, default=str)


def format_generic_table(items: List[Dict], title: str = "Items") -> str:
    """Format any list of dictionaries as a table - pure dynamic, no hardcoding."""
    if not items:
        return f"No {title.lower()} found."
    
    try:
        from rich.console import Console
        from rich.table import Table

        # Get all unique keys from all items
        all_keys = set()
        for item in items:
            all_keys.update(item.keys())
        
        # Create table with dynamic columns
        table = Table(show_header=True, header_style="bold magenta", show_lines=True, title=title)
        for key in sorted(all_keys):
            # Convert snake_case or camelCase to readable headers
            header = key.replace("_", " ").replace("Id", " ID").title()
            table.add_column(header)
        
        # Add rows with all data
        for item in items:
            row = [str(item.get(key, "N/A")) for key in sorted(all_keys)]
            table.add_row(*row)
        
        # Capture output
        console = Console()
        with console.capture() as capture:
            console.print(table)
        return capture.get()
        
    except ImportError:
        # Fallback to ASCII table if Rich is not available
        return _format_generic_ascii_table(items, title)


def format_generic_list(items: List[Dict], title: str = "Items") -> str:
    """Format any list of dictionaries as a detailed list - pure dynamic, no hardcoding."""
    if not items:
        return f"No {title.lower()} found."
    
    lines = [f"{title}:\n"]
    
    for i, item in enumerate(items):
        if i > 0:
            lines.append("")  # Blank line between items
        
        lines.append(f"{title[:-1]} {i + 1}:")
        for key in sorted(item.keys()):
            # Convert snake_case or camelCase to readable labels
            label = key.replace("_", " ").replace("Id", " ID").title()
            value = item.get(key, "N/A")
            lines.append(f"  {label}: {value}")
    
    return "\n".join(lines)


def _format_generic_ascii_table(items: List[Dict], title: str) -> str:
    """Fallback ASCII table formatter when Rich is not available - pure dynamic."""
    if not items:
        return f"No {title.lower()} found."
    
    # Get all unique keys
    all_keys = sorted(set().union(*(item.keys() for item in items)))
    
    # Create headers
    headers = [key.replace("_", " ").replace("Id", " ID").title() for key in all_keys]
    
    # Calculate column widths
    widths = [len(header) for header in headers]
    for item in items:
        for i, key in enumerate(all_keys):
            value_len = len(str(item.get(key, "N/A")))
            widths[i] = max(widths[i], value_len)
    
    # Format table
    lines = [f"\n{title}:"]
    
    # Header row
    header_row = " | ".join(header.ljust(width) for header, width in zip(headers, widths))
    lines.append(header_row)
    lines.append("-" * len(header_row))
    
    # Data rows
    for item in items:
        row_values = [str(item.get(key, "N/A")).ljust(width) for key, width in zip(all_keys, widths)]
        lines.append(" | ".join(row_values))
    
    return "\n".join(lines)
