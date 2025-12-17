"""
Output parsing utility for ORB test results.

This module provides functions to parse and display JSON output from
Host Factory Plugin operations in a readable format.
"""

import json
import logging
from typing import Any, Dict, Optional

# Set up logger
logger = logging.getLogger(__name__)


def parse_and_print_output(data: Any, title: Optional[str] = None) -> None:
    """
    Parse and print JSON output in a formatted way.

    Args:
        data: The data to parse and print (typically a dict or JSON string)
        title: Optional title to display before the output
    """
    try:
        # If data is a string, try to parse it as JSON
        if isinstance(data, str):
            try:
                parsed_data = json.loads(data)
            except json.JSONDecodeError:
                # If it's not valid JSON, just print as string
                parsed_data = data
        else:
            parsed_data = data

        # Print title if provided
        if title:
            print(f"\n=== {title} ===")

        # Pretty print the data
        if isinstance(parsed_data, (dict, list)):
            print(json.dumps(parsed_data, indent=2, default=str))
        else:
            print(str(parsed_data))

        print()  # Add blank line for readability

    except Exception as e:
        logger.error(f"Error parsing output: {e}")
        print(f"Raw output: {data}")


def format_request_response(response: Dict[str, Any]) -> str:
    """
    Format a request response for display.

    Args:
        response: The response dictionary from a request operation

    Returns:
        Formatted string representation of the response
    """
    try:
        if not isinstance(response, dict):
            return str(response)

        formatted_lines = []

        # Extract key information
        if "requestId" in response:
            formatted_lines.append(f"Request ID: {response['requestId']}")

        if "status" in response:
            formatted_lines.append(f"Status: {response['status']}")

        if "message" in response:
            formatted_lines.append(f"Message: {response['message']}")

        # Add full JSON for reference
        formatted_lines.append("\nFull Response:")
        formatted_lines.append(json.dumps(response, indent=2, default=str))

        return "\n".join(formatted_lines)

    except Exception as e:
        logger.error(f"Error formatting response: {e}")
        return json.dumps(response, indent=2, default=str)


def print_machine_status(machines: list) -> None:
    """
    Print machine status information in a tabular format.

    Args:
        machines: List of machine dictionaries
    """
    if not machines:
        print("No machines found.")
        return

    print("\nMachine Status:")
    print("-" * 80)
    print(f"{'Machine ID':<20} {'Status':<15} {'State':<15} {'Type':<15}")
    print("-" * 80)

    for machine in machines:
        machine_id = machine.get("machineId", "N/A")[:19]
        status = machine.get("status", "N/A")
        state = machine.get("state", "N/A")
        machine_type = machine.get("type", "N/A")

        print(f"{machine_id:<20} {status:<15} {state:<15} {machine_type:<15}")

    print("-" * 80)


def print_template_info(templates: list) -> None:
    """
    Print template information in a readable format.

    Args:
        templates: List of template dictionaries
    """
    if not templates:
        print("No templates found.")
        return

    print("\nAvailable Templates:")
    print("=" * 60)

    for i, template in enumerate(templates, 1):
        print(f"\nTemplate {i}:")
        print(f"  ID: {template.get('templateId', 'N/A')}")
        print(f"  Name: {template.get('name', 'N/A')}")
        print(f"  Description: {template.get('description', 'N/A')}")

        if "attributes" in template:
            print("  Attributes:")
            for key, value in template["attributes"].items():
                print(f"    {key}: {value}")

    print("=" * 60)


# Convenience function for backward compatibility
def parse_output(data: Any) -> None:
    """
    Legacy function name for parse_and_print_output.

    Args:
        data: The data to parse and print
    """
    parse_and_print_output(data)
