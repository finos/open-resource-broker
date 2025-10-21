"""HostFactory-specific field transformations."""

from typing import Any

from infrastructure.logging.logger import get_logger


class HostFactoryTransformations:
    """HostFactory-specific field transformations."""

    @staticmethod
    def transform_subnet_id(value: Any) -> list[str]:
        """Transform HostFactory subnetId field to subnet_ids list."""
        if isinstance(value, str):
            return [value]
        elif isinstance(value, list):
            return value
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
        Transform HostFactory user_data field by reading file content if it's a file path.

        If the value is a file path (starts with / or contains .sh/.ps1/.bat),
        read the file content. Otherwise, return the value as-is.
        """
        if not isinstance(value, str) or not value.strip():
            return value

        # Check if it looks like a file path
        if (
            value.startswith("/")
            or value.endswith(".sh")
            or value.endswith(".ps1")
            or value.endswith(".bat")
            or "/" in value
        ):
            try:
                import os

                # Convert to absolute path if relative
                if not os.path.isabs(value):
                    # Assume relative to current working directory
                    value = os.path.abspath(value)

                if os.path.isfile(value):
                    with open(value, encoding="utf-8") as f:
                        content = f.read()
                    logger = get_logger(__name__)
                    logger.info(
                        "Read user data script from file: %s (%d bytes)", value, len(content)
                    )
                    return content
                else:
                    logger = get_logger(__name__)
                    logger.warning("User data file not found: %s", value)
                    return value  # Return original value if file doesn't exist
            except Exception as e:
                logger = get_logger(__name__)
                logger.error("Failed to read user data file %s: %s", value, e)
                return value  # Return original value on error

        # Return as-is if it doesn't look like a file path
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
