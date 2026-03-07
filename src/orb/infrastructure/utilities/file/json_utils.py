"""JSON file operations utilities."""

import json
from typing import Any


def read_json_file(file_path: str, encoding: str = "utf-8") -> dict[str, Any]:
    """
    Read a JSON file and return parsed data.

    Args:
        file_path: Path to JSON file
        encoding: File encoding (default: utf-8)

    Returns:
        Parsed JSON data as dictionary

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If JSON parsing fails
    """
    try:
        with open(file_path, encoding=encoding) as f:
            result: dict[str, Any] = json.load(f)
            return result
    except FileNotFoundError:
        raise FileNotFoundError(f"JSON file not found: {file_path}")
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"Failed to parse JSON file {file_path}: {e!s}", e.doc, e.pos)


def write_json_file(
    file_path: str,
    data: dict[str, Any],
    encoding: str = "utf-8",
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    """
    Write data to a JSON file.

    Args:
        file_path: Path to JSON file
        data: Data to write
        encoding: File encoding (default: utf-8)
        indent: JSON indentation (default: 2)
        ensure_ascii: Whether to escape non-ASCII characters (default: False)

    Raises:
        OSError: If file cannot be written
        TypeError: If data cannot be serialized to JSON
    """
    from .directory_utils import ensure_parent_directory_exists

    try:
        ensure_parent_directory_exists(file_path)
        with open(file_path, "w", encoding=encoding) as f:
            json.dump(
                data,
                f,
                indent=indent,
                ensure_ascii=ensure_ascii,
                separators=(",", ": "),
            )
    except TypeError as e:
        raise TypeError(f"Failed to serialize data to JSON for {file_path}: {e!s}")
    except OSError as e:
        raise OSError(f"Failed to write JSON file {file_path}: {e!s}")
