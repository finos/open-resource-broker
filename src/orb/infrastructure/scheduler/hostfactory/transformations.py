"""HostFactory-specific field transformations."""

import os
from typing import Any

from orb.infrastructure.constants import MAX_FILE_SIZE_BYTES
from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.utilities.file.text_utils import read_text_file

_PATH_MAX = 4096


class HostFactoryTransformations:
    """HostFactory-specific field transformations."""

    @staticmethod
    def transform_subnet_id(value: Any) -> list[str]:
        """Transform AWS subnetId field to subnet_ids list.

        Handles all input shapes:
        - plain string: "subnet-abc" → ["subnet-abc"]
        - comma-separated string: "subnet-abc,subnet-def" → ["subnet-abc", "subnet-def"]
        - list of strings: ["subnet-abc", "subnet-def"] → ["subnet-abc", "subnet-def"]
        - list of comma-separated strings: ["subnet-abc,subnet-def"] → ["subnet-abc", "subnet-def"]
        - items may have whitespace: " subnet-abc , subnet-def " → ["subnet-abc", "subnet-def"]
        - None values and non-string items in lists are filtered out
        """
        if isinstance(value, str):
            return [s.strip() for s in value.split(",") if s.strip()]
        elif isinstance(value, list):
            result = []
            for item in value:
                if isinstance(item, str):
                    result.extend(s.strip() for s in item.split(",") if s.strip())
            return result
        else:
            return []

    @staticmethod
    def transform_instance_tags(value: Any) -> dict[str, str]:
        """
        Transform HostFactory instanceTags from string format to dict.

        HostFactory format: "key1=value1;key2=value2"
        Internal format: {"key1": "value1", "key2": "value2"}
        """
        if isinstance(value, str):
            tags = {}
            if value.strip():
                for tag_pair in value.split(";"):
                    if "=" in tag_pair:
                        key, val = tag_pair.split("=", 1)
                        tags[key.strip()] = val.strip()
            return tags
        elif isinstance(value, dict):
            return value
        else:
            return {}

    @staticmethod
    def transform_user_data(value: Any) -> str | None:
        """
        Read file content if `value` is a path to an existing file, else return `value` unchanged.

        Idempotent: running twice on already-read content returns it unchanged, because
        multi-line script content cannot be a valid filesystem path.
        """
        if not isinstance(value, str) or not value.strip():
            return value

        # Fast reject: values that cannot structurally be a filesystem path.
        if "\n" in value or "\x00" in value or len(value) > _PATH_MAX:
            return value

        logger = get_logger(__name__)
        candidate = os.path.expanduser(os.path.expandvars(value))

        try:
            if not os.path.isfile(candidate):
                return value
            size = os.path.getsize(candidate)
            if size > MAX_FILE_SIZE_BYTES:
                logger.warning(
                    "User data file exceeds size limit (%d > %d bytes), using literal value: %s",
                    size,
                    MAX_FILE_SIZE_BYTES,
                    candidate,
                )
                return value
            file_content = read_text_file(candidate)
            logger.info(
                "Read user data script from file: %s (%d bytes)", candidate, len(file_content)
            )
            return file_content
        except (OSError, UnicodeDecodeError) as e:
            logger.error("Failed to read user data file %s: %s", candidate, e)
            return value

    @staticmethod
    def ensure_instance_type_consistency(mapped_data: dict[str, Any]) -> dict[str, Any]:
        """
        Ensure instance_type and instance_types fields are consistent for HostFactory.

        If instance_types is provided but instance_type is not,
        set instance_type to the first instance type from instance_types.
        """
        if (
            "instance_types" in mapped_data
            and mapped_data["instance_types"]
            and ("instance_type" not in mapped_data or not mapped_data["instance_type"])
        ):
            # Set primary instance_type from first instance_types entry
            instance_types = mapped_data["instance_types"]
            if isinstance(instance_types, dict) and instance_types:
                mapped_data["instance_type"] = next(iter(instance_types.keys()))

        return mapped_data

    @staticmethod
    def apply_transformations(mapped_data: dict[str, Any]) -> dict[str, Any]:
        """Apply all HostFactory-specific field transformations."""
        logger = get_logger(__name__)

        # Transform subnet_ids
        if "subnet_ids" in mapped_data:
            original_value = mapped_data["subnet_ids"]
            mapped_data["subnet_ids"] = HostFactoryTransformations.transform_subnet_id(
                original_value
            )
            logger.debug(
                "HostFactory: Transformed subnet_ids: %s -> %s",
                original_value,
                mapped_data["subnet_ids"],
            )

        # Transform tags
        if "tags" in mapped_data:
            original_value = mapped_data["tags"]
            mapped_data["tags"] = HostFactoryTransformations.transform_instance_tags(original_value)
            logger.debug(
                "HostFactory: Transformed tags: %s -> %s",
                original_value,
                mapped_data["tags"],
            )

        # Transform user_data (read file content if it's a file path)
        if "user_data" in mapped_data:
            original_value = mapped_data["user_data"]
            mapped_data["user_data"] = HostFactoryTransformations.transform_user_data(
                original_value
            )
            if mapped_data["user_data"] != original_value:
                logger.debug(
                    "HostFactory: Transformed user_data from file path: %s -> %d bytes of content",
                    original_value,
                    len(mapped_data["user_data"]) if mapped_data["user_data"] else 0,
                )

        # Transform volume-related camelCase fields to snake_case
        if "rootDeviceVolumeSize" in mapped_data:
            mapped_data["root_device_volume_size"] = mapped_data["rootDeviceVolumeSize"]
            logger.debug(
                "HostFactory: Transformed rootDeviceVolumeSize: %s -> root_device_volume_size: %s",
                mapped_data["rootDeviceVolumeSize"],
                mapped_data["root_device_volume_size"],
            )

        if "volumeType" in mapped_data:
            mapped_data["volume_type"] = mapped_data["volumeType"]
            logger.debug(
                "HostFactory: Transformed volumeType: %s -> volume_type: %s",
                mapped_data["volumeType"],
                mapped_data["volume_type"],
            )

        # Ensure instance type consistency
        mapped_data = HostFactoryTransformations.ensure_instance_type_consistency(mapped_data)

        return mapped_data
