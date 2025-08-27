"""Infrastructure utilities - common utilities and factories."""

# Import common utilities
# Export abstract interface from canonical location
from domain.base import UnitOfWorkFactory
from infrastructure.utilities.common.collections import (
    filter_dict,
    group_by,
    transform_list,
    validate_collection,
)
from infrastructure.utilities.common.date_utils import (
    format_datetime,
    get_current_timestamp,
    parse_datetime,
)
from infrastructure.utilities.common.file_utils import (
    ensure_directory_exists,
    read_json_file,
    write_json_file,
)
from infrastructure.utilities.common.resource_naming import (
    get_asg_name,
    get_fleet_name,
    get_instance_name,
    get_launch_template_name,
    get_resource_prefix,
    get_tag_name,
)
from infrastructure.utilities.common.serialization import (
    deserialize_enum,
    process_value_objects,
    serialize_enum,
)
from infrastructure.utilities.common.string_utils import (
    mask_sensitive_data as sanitize_string,
    to_camel_case as snake_to_camel,
    to_snake_case as camel_to_snake,
    truncate as truncate_string,
)
from infrastructure.utilities.factories.api_handler_factory import APIHandlerFactory

# Import factories (removed legacy ProviderFactory)
from infrastructure.utilities.factories.repository_factory import RepositoryFactory
from infrastructure.utilities.factories.sql_engine_factory import SQLEngineFactory

__all__: list[str] = [
    "APIHandlerFactory",
    # Factories (legacy ProviderFactory removed)
    "RepositoryFactory",
    "SQLEngineFactory",
    "UnitOfWorkFactory",
    # String utilities
    "camel_to_snake",
    # String utilities (aliases)
    "camel_to_snake",
    "deserialize_enum",
    # File utilities
    "ensure_directory_exists",
    # Collection utilities
    "filter_dict",
    # Date utilities
    "format_datetime",
    "get_asg_name",
    "get_current_timestamp",
    "get_fleet_name",
    "get_instance_name",
    "get_launch_template_name",
    # Resource naming
    "get_resource_prefix",
    "get_tag_name",
    "group_by",
    "parse_datetime",
    "process_value_objects",
    "read_json_file",
    "sanitize_string",
    "sanitize_string",
    # Serialization
    "serialize_enum",
    "snake_to_camel",
    "snake_to_camel",
    "transform_list",
    "truncate_string",
    "truncate_string",
    "validate_collection",
    "write_json_file",
]
