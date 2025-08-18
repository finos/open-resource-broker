"""Core file operations - optimized for performance."""

import json
import os
import tempfile
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    pass


def ensure_directory_exists(directory_path: str) -> None:
    """
    Ensure a directory exists, creating it if necessary.

    Args:
        directory_path: Directory path

    Raises:
        OSError: If directory cannot be created
    """
    os.makedirs(directory_path, exist_ok=True)


def ensure_parent_directory_exists(file_path: str) -> None:
    """
    Ensure the parent directory of a file exists, creating it if necessary.

    Args:
        file_path: File path

    Raises:
        OSError: If directory cannot be created
    """
    directory = os.path.dirname(file_path)
    if directory:
        ensure_directory_exists(directory)


def read_text_file(file_path: str, encoding: str = "utf-8") -> str:
    """
    Read a text file.

    Args:
        file_path: File path
        encoding: File encoding

    Returns:
        File contents

    Raises:
        FileNotFoundError: If file doesn't exist
        IOError: If file cannot be read
    """
    with open(file_path, "r", encoding=encoding) as f:
        return f.read()


def write_text_file(file_path: str, content: str, encoding: str = "utf-8") -> None:
    """
    Write content to a text file.

    Args:
        file_path: File path
        content: Content to write
        encoding: File encoding

    Raises:
        IOError: If file cannot be written
    """
    ensure_parent_directory_exists(file_path)
    with open(file_path, "w", encoding=encoding) as f:
        f.write(content)


def read_json_file(file_path: str, encoding: str = "utf-8") -> Dict[str, Any]:
    """
    Read a JSON file.

    Args:
        file_path: File path
        encoding: File encoding

    Returns:
        Parsed JSON data

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If file is not valid JSON
    """
    with open(file_path, "r", encoding=encoding) as f:
        result: Dict[str, Any] = json.load(f)
        return result


def write_json_file(
    file_path: str, data: Dict[str, Any], encoding: str = "utf-8", indent: int = 2
) -> None:
    """
    Write data to a JSON file.

    Args:
        file_path: File path
        data: Data to write
        encoding: File encoding
        indent: JSON indentation

    Raises:
        IOError: If file cannot be written
    """
    ensure_parent_directory_exists(file_path)
    with open(file_path, "w", encoding=encoding) as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)


def file_exists(file_path: str) -> bool:
    """Check if file exists."""
    return os.path.isfile(file_path)


def directory_exists(directory_path: str) -> bool:
    """Check if directory exists."""
    return os.path.isdir(directory_path)


def get_file_size(file_path: str) -> int:
    """Get file size in bytes."""
    return os.path.getsize(file_path)


def create_temp_file(
    suffix: Optional[str] = None,
    prefix: Optional[str] = None,
    dir: Optional[str] = None,
) -> str:
    """
    Create a temporary file and return its path.

    Args:
        suffix: File suffix
        prefix: File prefix
        dir: Directory to create file in

    Returns:
        Path to temporary file
    """
    fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=dir)
    os.close(fd)  # Close file descriptor
    return path


def create_temp_directory(
    suffix: Optional[str] = None,
    prefix: Optional[str] = None,
    dir: Optional[str] = None,
) -> str:
    """
    Create a temporary directory and return its path.

    Args:
        suffix: Directory suffix
        prefix: Directory prefix
        dir: Parent directory

    Returns:
        Path to temporary directory
    """
    return tempfile.mkdtemp(suffix=suffix, prefix=prefix, dir=dir)
