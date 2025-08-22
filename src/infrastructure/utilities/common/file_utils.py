"""
File utility functions for the AWS Host Factory Plugin - Refactored.

This module now imports from the organized file utilities package.
All functions maintain backward compatibility.
"""

# Import only the functions that are actually used/exported
from infrastructure.utilities.file import (  # YAML operations; JSON operations; Text operations; Binary operations; Directory operations; File operations; Path operations; Utility functions
    append_text_file,
    copy_file,
    create_temp_directory,
    create_temp_file,
    delete_directory,
    delete_file,
    directory_exists,
    ensure_directory_exists,
    ensure_parent_directory_exists,
    file_exists,
    find_files,
    get_absolute_path,
    get_directory_name,
    get_file_extension,
    get_file_hash,
    get_file_name,
    get_file_name_without_extension,
    get_file_size,
    get_file_utils_logger,  # Import the actual function instead of redefining
    get_relative_path,
    is_binary_file,
    is_text_file,
    join_paths,
    list_directories,
    list_files,
    move_file,
    normalize_path,
    read_binary_file,
    read_json_file,
    read_text_file,
    read_yaml_file,
    rename_file,
    touch_file,
    with_temp_file,
    write_binary_file,
    write_json_file,
    write_text_file,
    write_yaml_file,
)


def _get_logger():
    """Legacy logger function."""
    return get_file_utils_logger()


# Re-export commonly used functions to maintain existing imports
__all__: list[str] = [
    # YAML operations
    "read_yaml_file",
    "write_yaml_file",
    # JSON operations
    "read_json_file",
    "write_json_file",
    # Text operations
    "read_text_file",
    "write_text_file",
    "append_text_file",
    # Binary operations
    "read_binary_file",
    "write_binary_file",
    "get_file_hash",
    "is_binary_file",
    "is_text_file",
    # Directory operations
    "ensure_directory_exists",
    "ensure_parent_directory_exists",
    "directory_exists",
    "delete_directory",
    "list_files",
    "list_directories",
    "find_files",
    "create_temp_directory",
    # File operations
    "file_exists",
    "get_file_size",
    "delete_file",
    "copy_file",
    "move_file",
    "rename_file",
    "touch_file",
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
    # Utility functions
    "get_file_utils_logger",
]
