"""
File utilities package - Organized file operations.

This package provides comprehensive file operations organized by functionality:
- YAML operations: read_yaml_file, write_yaml_file
- JSON operations: read_json_file, write_json_file
- Text operations: read_text_file, write_text_file, append_text_file
- Binary operations: read_binary_file, write_binary_file
- Directory operations: list_files, list_directories, ensure_directory_exists
- File operations: copy_file, move_file, delete_file, file_exists

All functions include structured error handling and type hints.
"""

# Binary operations
from .binary_utils import (
    append_binary_file,
    get_file_hash,
    get_file_mime_type,
    is_binary_file,
    is_text_file,
    read_binary_file,
    write_binary_file,
)

# Directory operations
from .directory_utils import (
    change_directory,
    create_temp_directory,
    delete_directory,
    directory_exists,
    ensure_directory_exists,
    ensure_parent_directory_exists,
    find_files,
    get_current_directory,
    get_home_directory,
    list_directories,
    list_files,
)

# File operations
from .file_operations import (
    copy_file,
    create_temp_file,
    delete_file,
    file_exists,
    get_absolute_path,
    get_directory_name,
    get_file_access_time,
    get_file_creation_time,
    get_file_extension,
    get_file_group,
    get_file_modification_time,
    get_file_name,
    get_file_name_without_extension,
    get_file_owner,
    get_file_permissions,
    get_file_size,
    get_relative_path,
    is_file_empty,
    join_paths,
    move_file,
    normalize_path,
    rename_file,
    set_file_owner_and_group,
    set_file_permissions,
    touch_file,
    with_temp_file,
)

# JSON operations
from .json_utils import read_json_file, write_json_file

# Text operations
from .text_utils import (
    append_text_file,
    read_text_file,
    read_text_lines,
    write_text_file,
    write_text_lines,
)

# YAML operations
from .yaml_utils import read_yaml_file, write_yaml_file

# Backward compatibility - commonly used functions
__all__: list[str] = [
    # YAML
    "read_yaml_file",
    "write_yaml_file",
    # JSON
    "read_json_file",
    "write_json_file",
    # Text
    "read_text_file",
    "write_text_file",
    "append_text_file",
    "read_text_lines",
    "write_text_lines",
    # Binary
    "read_binary_file",
    "write_binary_file",
    "append_binary_file",
    "get_file_hash",
    "get_file_mime_type",
    "is_binary_file",
    "is_text_file",
    # Directory
    "ensure_directory_exists",
    "ensure_parent_directory_exists",
    "directory_exists",
    "delete_directory",
    "list_files",
    "list_directories",
    "find_files",
    "get_current_directory",
    "change_directory",
    "get_home_directory",
    "create_temp_directory",
    # File operations
    "file_exists",
    "get_file_size",
    "get_file_modification_time",
    "get_file_creation_time",
    "get_file_access_time",
    "delete_file",
    "copy_file",
    "move_file",
    "rename_file",
    "touch_file",
    "is_file_empty",
    "create_temp_file",
    "with_temp_file",
    "get_file_extension",
    "get_file_name",
    "get_file_name_without_extension",
    "get_directory_name",
    "get_absolute_path",
    "get_relative_path",
    "join_paths",
    "normalize_path",
    "get_file_permissions",
    "set_file_permissions",
    "get_file_owner",
    "get_file_group",
    "set_file_owner_and_group",
]


def get_file_utils_logger():
    """Get logger for file utilities."""
    from infrastructure.logging.logger import get_logger

    return get_logger(__name__)
