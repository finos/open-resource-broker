"""Text file operations utilities."""

from typing import List


def read_text_file(file_path: str, encoding: str = "utf-8") -> str:
    """
    Read a text file and return its contents.

    Args:
        file_path: Path to text file
        encoding: File encoding (default: utf-8)

    Returns:
        File contents as string

    Raises:
        FileNotFoundError: If file doesn't exist
        UnicodeDecodeError: If file cannot be decoded with specified encoding
    """
    try:
        with open(file_path, "r", encoding=encoding) as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Text file not found: {file_path}") from e
    except UnicodeDecodeError as e:
        raise UnicodeDecodeError(
            e.encoding,
            e.object,
            e.start,
            e.end,
            f"Failed to decode text file {file_path}: {e.reason}",
        ) from e


def write_text_file(file_path: str, content: str, encoding: str = "utf-8") -> None:
    """
    Write text content to a file.

    Args:
        file_path: Path to text file
        content: Text content to write
        encoding: File encoding (default: utf-8)

    Raises:
        OSError: If file cannot be written
    """
    from .directory_utils import ensure_parent_directory_exists

    try:
        ensure_parent_directory_exists(file_path)
        with open(file_path, "w", encoding=encoding) as f:
            f.write(content)
    except OSError as e:
        raise OSError(f"Failed to write text file {file_path}: {str(e)}")


def append_text_file(file_path: str, content: str, encoding: str = "utf-8") -> None:
    """
    Append text content to a file.

    Args:
        file_path: Path to text file
        content: Text content to append
        encoding: File encoding (default: utf-8)

    Raises:
        OSError: If file cannot be written
    """
    from .directory_utils import ensure_parent_directory_exists

    try:
        ensure_parent_directory_exists(file_path)
        with open(file_path, "a", encoding=encoding) as f:
            f.write(content)
    except OSError as e:
        raise OSError(f"Failed to append to text file {file_path}: {str(e)}")


def read_text_lines(
    file_path: str, encoding: str = "utf-8", strip_whitespace: bool = True
) -> List[str]:
    """
    Read a text file and return its lines.

    Args:
        file_path: Path to text file
        encoding: File encoding (default: utf-8)
        strip_whitespace: Whether to strip whitespace from lines (default: True)

    Returns:
        List of lines from the file

    Raises:
        FileNotFoundError: If file doesn't exist
        UnicodeDecodeError: If file cannot be decoded with specified encoding
    """
    try:
        with open(file_path, "r", encoding=encoding) as f:
            lines = f.readlines()
            if strip_whitespace:
                lines = [line.strip() for line in lines]
            return lines
    except FileNotFoundError:
        raise FileNotFoundError(f"Text file not found: {file_path}") from e
    except UnicodeDecodeError as e:
        raise UnicodeDecodeError(
            e.encoding,
            e.object,
            e.start,
            e.end,
            f"Failed to decode text file {file_path}: {e.reason}",
        ) from e


def write_text_lines(
    file_path: str, lines: List[str], encoding: str = "utf-8", add_newlines: bool = True
) -> None:
    """
    Write lines of text to a file.

    Args:
        file_path: Path to text file
        lines: List of text lines to write
        encoding: File encoding (default: utf-8)
        add_newlines: Whether to add newlines to each line (default: True)

    Raises:
        OSError: If file cannot be written
    """
    from .directory_utils import ensure_parent_directory_exists

    try:
        ensure_parent_directory_exists(file_path)
        with open(file_path, "w", encoding=encoding) as f:
            for line in lines:
                if add_newlines and not line.endswith("\n"):
                    line += "\n"
                f.write(line)
    except OSError as e:
        raise OSError(f"Failed to write text lines to {file_path}: {str(e)}")
