"""Basic file operations utilities."""

import os
import shutil
import tempfile
from typing import ContextManager, Optional


def file_exists(file_path: str) -> bool:
    """
    Check if a file exists.

    Args:
        file_path: Path to file

    Returns:
        True if file exists, False otherwise
    """
    return os.path.isfile(file_path)


def get_file_size(file_path: str) -> int:
    """
    Get size of a file in bytes.

    Args:
        file_path: Path to file

    Returns:
        File size in bytes

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If file stats cannot be retrieved
    """
    try:
        return os.path.getsize(file_path)
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}") from e
    except OSError as e:
        raise OSError(f"Failed to get file size for {file_path}: {str(e)}")


def get_file_modification_time(file_path: str) -> float:
    """
    Get file modification time as timestamp.

    Args:
        file_path: Path to file

    Returns:
        Modification time as timestamp

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If file stats cannot be retrieved
    """
    try:
        return os.path.getmtime(file_path)
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}") from e
    except OSError as e:
        raise OSError(f"Failed to get modification time for {file_path}: {str(e)}")


def get_file_creation_time(file_path: str) -> float:
    """
    Get file creation time as timestamp.

    Args:
        file_path: Path to file

    Returns:
        Creation time as timestamp

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If file stats cannot be retrieved
    """
    try:
        return os.path.getctime(file_path)
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}") from e
    except OSError as e:
        raise OSError(f"Failed to get creation time for {file_path}: {str(e)}")


def get_file_access_time(file_path: str) -> float:
    """
    Get file access time as timestamp.

    Args:
        file_path: Path to file

    Returns:
        Access time as timestamp

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If file stats cannot be retrieved
    """
    try:
        return os.path.getatime(file_path)
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}") from e
    except OSError as e:
        raise OSError(f"Failed to get access time for {file_path}: {str(e)}")


def delete_file(file_path: str) -> None:
    """
    Delete a file.

    Args:
        file_path: Path to file

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If file cannot be deleted
    """
    if not file_exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}") from e

    try:
        os.remove(file_path)
    except OSError as e:
        raise OSError(f"Failed to delete file {file_path}: {str(e)}")


def copy_file(source_path: str, destination_path: str) -> None:
    """
    Copy a file from source to destination.

    Args:
        source_path: Source file path
        destination_path: Destination file path

    Raises:
        FileNotFoundError: If source file doesn't exist
        OSError: If file cannot be copied
    """
    from .directory_utils import ensure_parent_directory_exists

    if not file_exists(source_path):
        raise FileNotFoundError(f"Source file not found: {source_path}") from e

    try:
        ensure_parent_directory_exists(destination_path)
        shutil.copy2(source_path, destination_path)
    except OSError as e:
        raise OSError(f"Failed to copy file from {source_path} to {destination_path}: {str(e)}")


def move_file(source_path: str, destination_path: str) -> None:
    """
    Move a file from source to destination.

    Args:
        source_path: Source file path
        destination_path: Destination file path

    Raises:
        FileNotFoundError: If source file doesn't exist
        OSError: If file cannot be moved
    """
    from .directory_utils import ensure_parent_directory_exists

    if not file_exists(source_path):
        raise FileNotFoundError(f"Source file not found: {source_path}") from e

    try:
        ensure_parent_directory_exists(destination_path)
        shutil.move(source_path, destination_path)
    except OSError as e:
        raise OSError(f"Failed to move file from {source_path} to {destination_path}: {str(e)}")


def rename_file(file_path: str, new_name: str) -> str:
    """
    Rename a file.

    Args:
        file_path: Current file path
        new_name: New file name (not full path)

    Returns:
        New file path

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If file cannot be renamed
    """
    if not file_exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}") from e

    directory = os.path.dirname(file_path)
    new_path = os.path.join(directory, new_name)

    try:
        os.rename(file_path, new_path)
        return new_path
    except OSError as e:
        raise OSError(f"Failed to rename file {file_path} to {new_name}: {str(e)}")


def touch_file(file_path: str) -> None:
    """
    Create an empty file or update its timestamp.

    Args:
        file_path: Path to file

    Raises:
        OSError: If file cannot be created or touched
    """
    from .directory_utils import ensure_parent_directory_exists

    try:
        ensure_parent_directory_exists(file_path)
        with open(file_path, "a"):
            os.utime(file_path, None)
    except OSError as e:
        raise OSError(f"Failed to touch file {file_path}: {str(e)}")


def is_file_empty(file_path: str) -> bool:
    """
    Check if a file is empty.

    Args:
        file_path: Path to file

    Returns:
        True if file is empty, False otherwise

    Raises:
        FileNotFoundError: If file doesn't exist
    """
    if not file_exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}") from e

    return get_file_size(file_path) == 0


def create_temp_file(suffix: str = "", prefix: str = "", dir: str = None) -> str:
    """
    Create a temporary file.

    Args:
        suffix: File name suffix
        prefix: File name prefix
        dir: Parent directory (default: system temp)

    Returns:
        Path to created temporary file

    Raises:
        OSError: If temporary file cannot be created
    """
    try:
        fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=dir)
        os.close(fd)  # Close the file descriptor
        return path
    except OSError as e:
        raise OSError(f"Failed to create temporary file: {str(e)}")


def with_temp_file(suffix: str = "", prefix: str = "", dir: str = None) -> ContextManager[str]:
    """
    Context manager for temporary file.

    Args:
        suffix: File name suffix
        prefix: File name prefix
        dir: Parent directory (default: system temp)

    Returns:
        Context manager yielding temporary file path

    Example:
        with with_temp_file(suffix='.txt') as temp_path:
            write_text_file(temp_path, "Hello World")
            # File is automatically deleted when exiting context
    """
    import contextlib

    @contextlib.contextmanager
    def temp_file_context():
        """Context manager for temporary file creation and cleanup."""
        temp_path = create_temp_file(suffix=suffix, prefix=prefix, dir=dir)
        try:
            yield temp_path
        finally:
            if file_exists(temp_path):
                delete_file(temp_path)

    return temp_file_context()


def get_file_extension(file_path: str) -> str:
    """
    Get file extension.

    Args:
        file_path: Path to file

    Returns:
        File extension (including dot)
    """
    return os.path.splitext(file_path)[1]


def get_file_name(file_path: str) -> str:
    """
    Get file name from path.

    Args:
        file_path: Path to file

    Returns:
        File name with extension
    """
    return os.path.basename(file_path)


def get_file_name_without_extension(file_path: str) -> str:
    """
    Get file name without extension.

    Args:
        file_path: Path to file

    Returns:
        File name without extension
    """
    return os.path.splitext(os.path.basename(file_path))[0]


def get_directory_name(file_path: str) -> str:
    """
    Get directory name from file path.

    Args:
        file_path: Path to file

    Returns:
        Directory path
    """
    return os.path.dirname(file_path)


def get_absolute_path(file_path: str) -> str:
    """
    Get absolute path.

    Args:
        file_path: File path (relative or absolute)

    Returns:
        Absolute file path
    """
    return os.path.abspath(file_path)


def get_relative_path(file_path: str, start: Optional[str] = None) -> str:
    """
    Get relative path.

    Args:
        file_path: File path
        start: Start directory (default: current directory)

    Returns:
        Relative file path
    """
    if start is None:
        start = os.getcwd()
    return os.path.relpath(file_path, start)


def join_paths(*paths: str) -> str:
    """
    Join multiple path components.

    Args:
        *paths: Path components to join

    Returns:
        Joined path
    """
    return os.path.join(*paths)


def normalize_path(file_path: str) -> str:
    """
    Normalize a path.

    Args:
        file_path: Path to normalize

    Returns:
        Normalized path
    """
    return os.path.normpath(file_path)


def get_file_permissions(file_path: str) -> int:
    """
    Get file permissions.

    Args:
        file_path: Path to file

    Returns:
        File permissions as octal integer

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If permissions cannot be retrieved
    """
    try:
        return os.stat(file_path).st_mode & 0o777
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}") from e
    except OSError as e:
        raise OSError(f"Failed to get permissions for {file_path}: {str(e)}")


def set_file_permissions(file_path: str, permissions: int) -> None:
    """
    Set file permissions.

    Args:
        file_path: Path to file
        permissions: Permissions as octal integer (e.g., 0o644)

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If permissions cannot be set
    """
    if not file_exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}") from e

    try:
        os.chmod(file_path, permissions)
    except OSError as e:
        raise OSError(f"Failed to set permissions for {file_path}: {str(e)}")


def get_file_owner(file_path: str) -> int:
    """
    Get file owner UID.

    Args:
        file_path: Path to file

    Returns:
        Owner UID

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If owner cannot be retrieved
    """
    try:
        return os.stat(file_path).st_uid
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}") from e
    except OSError as e:
        raise OSError(f"Failed to get owner for {file_path}: {str(e)}")


def get_file_group(file_path: str) -> int:
    """
    Get file group GID.

    Args:
        file_path: Path to file

    Returns:
        Group GID

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If group cannot be retrieved
    """
    try:
        return os.stat(file_path).st_gid
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}") from e
    except OSError as e:
        raise OSError(f"Failed to get group for {file_path}: {str(e)}")


def set_file_owner_and_group(file_path: str, owner: int, group: int) -> None:
    """
    Set file owner and group.

    Args:
        file_path: Path to file
        owner: Owner UID
        group: Group GID

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If owner/group cannot be set
    """
    if not file_exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}") from e

    try:
        os.chown(file_path, owner, group)
    except OSError as e:
        raise OSError(f"Failed to set owner/group for {file_path}: {str(e)}")
