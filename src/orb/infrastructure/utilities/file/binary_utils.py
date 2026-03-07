"""Binary file operations utilities."""


def read_binary_file(file_path: str) -> bytes:
    """
    Read a binary file and return its contents.

    Args:
        file_path: Path to binary file

    Returns:
        File contents as bytes

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If file cannot be read
    """
    try:
        with open(file_path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Binary file not found: {file_path}")
    except OSError as e:
        raise OSError(f"Failed to read binary file {file_path}: {e!s}")


def write_binary_file(file_path: str, content: bytes) -> None:
    """
    Write binary content to a file.

    Args:
        file_path: Path to binary file
        content: Binary content to write

    Raises:
        OSError: If file cannot be written
        TypeError: If content is not bytes
    """
    from .directory_utils import ensure_parent_directory_exists

    if not isinstance(content, bytes):
        raise TypeError(f"Content must be bytes, got {type(content)}")

    try:
        ensure_parent_directory_exists(file_path)
        with open(file_path, "wb") as f:
            f.write(content)
    except OSError as e:
        raise OSError(f"Failed to write binary file {file_path}: {e!s}")


def append_binary_file(file_path: str, content: bytes) -> None:
    """
    Append binary content to a file.

    Args:
        file_path: Path to binary file
        content: Binary content to append

    Raises:
        OSError: If file cannot be written
        TypeError: If content is not bytes
    """
    from .directory_utils import ensure_parent_directory_exists

    if not isinstance(content, bytes):
        raise TypeError(f"Content must be bytes, got {type(content)}")

    try:
        ensure_parent_directory_exists(file_path)
        with open(file_path, "ab") as f:
            f.write(content)
    except OSError as e:
        raise OSError(f"Failed to append to binary file {file_path}: {e!s}")


def get_file_hash(file_path: str, algorithm: str = "sha256") -> str:
    """
    Calculate hash of a file.

    Args:
        file_path: Path to file
        algorithm: Hash algorithm (sha256, md5, sha1, etc.)

    Returns:
        Hexadecimal hash string

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If algorithm is not supported
        OSError: If file cannot be read
    """
    import hashlib

    try:
        hasher = hashlib.new(algorithm)
    except ValueError:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")

    try:
        with open(file_path, "rb") as f:
            # Read file in chunks to handle large files efficiently
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")
    except OSError as e:
        raise OSError(f"Failed to read file for hashing {file_path}: {e!s}")


def get_file_mime_type(file_path: str) -> str:
    """
    Get MIME type of a file.

    Args:
        file_path: Path to file

    Returns:
        MIME type string

    Raises:
        FileNotFoundError: If file doesn't exist
    """
    import mimetypes
    import os

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or "application/octet-stream"


def is_binary_file(file_path: str) -> bool:
    """
    Check if a file is binary.

    Args:
        file_path: Path to file

    Returns:
        True if file is binary, False otherwise

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If file cannot be read
    """
    try:
        with open(file_path, "rb") as f:
            # Read first 1024 bytes to check for binary content
            chunk = f.read(1024)
            if b"\0" in chunk:
                return True
            # Check for high ratio of non-printable characters
            text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7F})
            return bool(chunk.translate(None, text_chars))
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")
    except OSError as e:
        raise OSError(f"Failed to read file {file_path}: {e!s}")


def is_text_file(file_path: str) -> bool:
    """
    Check if a file is text.

    Args:
        file_path: Path to file

    Returns:
        True if file is text, False otherwise

    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If file cannot be read
    """
    return not is_binary_file(file_path)
