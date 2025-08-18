"""YAML file operations utilities."""

from typing import Any, Dict


def read_yaml_file(file_path: str, encoding: str = "utf-8") -> Dict[str, Any]:
    """
    Read a YAML file and return parsed data.

    Args:
        file_path: Path to YAML file
        encoding: File encoding (default: utf-8)

    Returns:
        Parsed YAML data as dictionary

    Raises:
        FileNotFoundError: If file doesn't exist
        yaml.YAMLError: If YAML parsing fails
    """
    import yaml

    try:
        with open(file_path, "r", encoding=encoding) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise FileNotFoundError(f"YAML file not found: {file_path}") from e
    except yaml.YAMLError as e:
        raise yaml.YAMLError(f"Failed to parse YAML file {file_path}: {str(e)}")


def write_yaml_file(file_path: str, data: Dict[str, Any], encoding: str = "utf-8") -> None:
    """
    Write data to a YAML file.

    Args:
        file_path: Path to YAML file
        data: Data to write
        encoding: File encoding (default: utf-8)

    Raises:
        OSError: If file cannot be written
        yaml.YAMLError: If data cannot be serialized to YAML
    """
    import yaml

    from .directory_utils import ensure_parent_directory_exists

    try:
        ensure_parent_directory_exists(file_path)
        with open(file_path, "w", encoding=encoding) as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
    except yaml.YAMLError as e:
        raise yaml.YAMLError(f"Failed to serialize data to YAML for {file_path}: {str(e)}")
    except OSError as e:
        raise OSError(f"Failed to write YAML file {file_path}: {str(e)}")
