"""HostFactory-specific field transformations."""

from typing import Any, Dict, List

from infrastructure.logging.logger import get_logger


class HostFactoryTransformations:
    """HostFactory-specific field transformations."""

    @staticmethod
    def transform_subnet_id(value: Any) -> List[str]:
        """Transform HostFactory subnetId field to subnet_ids list."""
        if isinstance(value, str):
            return [value]
        elif isinstance(value, list):
            return value
        else:
            return []

    @staticmethod
    def transform_instance_tags(value: Any) -> Dict[str, str]:
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
    def ensure_instance_type_consistency(mapped_data: Dict[str, Any]) -> Dict[str, Any]:
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
                mapped_data["instance_type"] = list(instance_types.keys())[0]

        return mapped_data

    @staticmethod
    def apply_transformations(mapped_data: Dict[str, Any]) -> Dict[str, Any]:
        """Apply all HostFactory-specific field transformations."""
        logger = get_logger(__name__)

        # Transform subnet_ids
        if "subnet_ids" in mapped_data:
            original_value = mapped_data["subnet_ids"]
            mapped_data["subnet_ids"] = HostFactoryTransformations.transform_subnet_id(
                original_value
            )
            logger.debug(
                "HostFactory: Transformed subnet_ids: %s -> %s", original_value, mapped_data['subnet_ids']
            )

        # Transform tags
        if "tags" in mapped_data:
            original_value = mapped_data["tags"]
            mapped_data["tags"] = HostFactoryTransformations.transform_instance_tags(original_value)
            logger.debug(
                "HostFactory: Transformed tags: %s -> %s", original_value, mapped_data['tags']
            )

        # Ensure instance type consistency
        mapped_data = HostFactoryTransformations.ensure_instance_type_consistency(mapped_data)

        return mapped_data
