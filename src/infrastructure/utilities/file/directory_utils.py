"""Directory operations utilities."""

import glob
import os
from typing import List, Optional


def ensure_directory_exists(directory_path: str) -> None:
    """
    Ensure a directory exists, creating it if necessary.

    Args:
        directory_path: Path to directory

    Raises:
        OSError: If directory cannot be created
    """
    try:
        os.makedirs(directory_path, exist_ok=True)
    except OSError as e:
        raise OSError(f"Failed to create directory {directory_path}: {str(e)}")


def ensure_parent_directory_exists(file_path: str) -> None:
    """
    Ensure the parent directory of a file exists.

    Args:
        file_path: Path to file

    Raises:
        OSError: If parent directory cannot be created
    """
    parent_dir = os.path.dirname(file_path)
    if parent_dir:
        ensure_directory_exists(parent_dir)


def directory_exists(directory_path: str) -> bool:
    """
    Check if a directory exists.

    Args:
        directory_path: Path to directory

    Returns:
        True if directory exists, False otherwise
    """
    return os.path.isdir(directory_path)


def delete_directory(directory_path: str, recursive: bool = False) -> None:
    """
    Delete a directory.

    Args:
        directory_path: Path to directory
        recursive: Whether to delete recursively (default: False)

    Raises:
        FileNotFoundError: If directory doesn't exist
        OSError: If directory cannot be deleted
    """
    import shutil

    if not directory_exists(directory_path):
        raise FileNotFoundError(f"Directory not found: {directory_path}") from e

    try:
        if recursive:
            shutil.rmtree(directory_path)
        else:
            os.rmdir(directory_path)
    except OSError as e:
        raise OSError(f"Failed to delete directory {directory_path}: {str(e)}")


def list_files(
    directory_path: str, pattern: Optional[str] = None, recursive: bool = False
) -> List[str]:
    """
    List files in a directory.

    Args:
        directory_path: Path to directory
        pattern: File pattern to match (e.g., "*.py")
        recursive: Whether to search recursively (default: False)

    Returns:
        List of file paths

    Raises:
        FileNotFoundError: If directory doesn't exist
    """
    if not directory_exists(directory_path):
        raise FileNotFoundError(f"Directory not found: {directory_path}") from e

    if pattern:
        if recursive:
            search_pattern = os.path.join(directory_path, "**", pattern)
            return glob.glob(search_pattern, recursive=True)
        else:
            search_pattern = os.path.join(directory_path, pattern)
            return glob.glob(search_pattern)
    else:
        files = []
        if recursive:
            for root, _, filenames in os.walk(directory_path):
                for filename in filenames:
                    files.append(os.path.join(root, filename))
        else:
            for item in os.listdir(directory_path):
                item_path = os.path.join(directory_path, item)
                if os.path.isfile(item_path):
                    files.append(item_path)
        return files


def list_directories(directory_path: str, recursive: bool = False) -> List[str]:
    """
    List directories in a directory.

    Args:
        directory_path: Path to directory
        recursive: Whether to search recursively (default: False)

    Returns:
        List of directory paths

    Raises:
        FileNotFoundError: If directory doesn't exist
    """
    if not directory_exists(directory_path):
        raise FileNotFoundError(f"Directory not found: {directory_path}") from e

    directories = []
    if recursive:
        for root, dirnames, _ in os.walk(directory_path):
            for dirname in dirnames:
                directories.append(os.path.join(root, dirname))
    else:
        for item in os.listdir(directory_path):
            item_path = os.path.join(directory_path, item)
            if os.path.isdir(item_path):
                directories.append(item_path)
    return directories


def find_files(
    directory_path: str,
    name_pattern: Optional[str] = None,
    content_pattern: Optional[str] = None,
    recursive: bool = True,
) -> List[str]:
    """
    Find files matching criteria.

    Args:
        directory_path: Path to directory to search
        name_pattern: File name pattern (e.g., "*.py")
        content_pattern: Content pattern to search for
        recursive: Whether to search recursively (default: True)

    Returns:
        List of matching file paths

    Raises:
        FileNotFoundError: If directory doesn't exist
    """
    if not directory_exists(directory_path):
        raise FileNotFoundError(f"Directory not found: {directory_path}") from e

    matching_files = []

    # Get files matching name pattern
    if name_pattern:
        files = list_files(directory_path, name_pattern, recursive)
    else:
        files = list_files(directory_path, recursive=recursive)

    # Filter by content pattern if specified
    if content_pattern:
        import re

        pattern = re.compile(content_pattern)
        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    if pattern.search(f.read()):
                        matching_files.append(file_path)
            except (OSError, UnicodeDecodeError):
                # Skip files that can't be read as text
                continue
    else:
        matching_files = files

    return matching_files


def get_current_directory() -> str:
    """
    Get current working directory.

    Returns:
        Current directory path
    """
    return os.getcwd()


def change_directory(directory_path: str) -> None:
    """
    Change current working directory.

    Args:
        directory_path: Path to directory

    Raises:
        FileNotFoundError: If directory doesn't exist
        OSError: If directory cannot be changed to
    """
    if not directory_exists(directory_path):
        raise FileNotFoundError(f"Directory not found: {directory_path}") from e

    try:
        os.chdir(directory_path)
    except OSError as e:
        raise OSError(f"Failed to change to directory {directory_path}: {str(e)}")


def get_home_directory() -> str:
    """
    Get user's home directory.

    Returns:
        Home directory path
    """
    return os.path.expanduser("~")


def create_temp_directory(suffix: str = "", prefix: str = "", dir: str = None) -> str:
    """
    Create a temporary directory.

    Args:
        suffix: Directory name suffix
        prefix: Directory name prefix
        dir: Parent directory (default: system temp)

    Returns:
        Path to created temporary directory

    Raises:
        OSError: If temporary directory cannot be created
    """
    import tempfile

    try:
        return tempfile.mkdtemp(suffix=suffix, prefix=prefix, dir=dir)
    except OSError as e:
        raise OSError(f"Failed to create temporary directory: {str(e)}")
