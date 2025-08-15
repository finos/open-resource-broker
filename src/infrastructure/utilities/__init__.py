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
)
from infrastructure.utilities.common.string_utils import (
    to_camel_case as snake_to_camel,
)
from infrastructure.utilities.common.string_utils import (
    to_snake_case as camel_to_snake,
)
from infrastructure.utilities.common.string_utils import truncate as truncate_string
from infrastructure.utilities.factories.api_handler_factory import APIHandlerFactory

# Import factories (removed legacy ProviderFactory)
from infrastructure.utilities.factories.repository_factory import RepositoryFactory
from infrastructure.utilities.factories.sql_engine_factory import SQLEngineFactory

__all__ = [
    # String utilities
    "camel_to_snake",
    "snake_to_camel",
    "sanitize_string",
    "truncate_string",
    # Date utilities
    "format_datetime",
    "parse_datetime",
    "get_current_timestamp",
    # File utilities
    "ensure_directory_exists",
    "read_json_file",
    "write_json_file",
    # Collection utilities
    "filter_dict",
    "group_by",
    "transform_list",
    "validate_collection",
    # Resource naming
    "get_resource_prefix",
    "get_launch_template_name",
    "get_instance_name",
    "get_fleet_name",
    "get_asg_name",
    "get_tag_name",
    # Serialization
    "serialize_enum",
    "deserialize_enum",
    "process_value_objects",
    # String utilities (aliases)
    "camel_to_snake",
    "snake_to_camel",
    "sanitize_string",
    "truncate_string",
    # Factories (legacy ProviderFactory removed)
    "RepositoryFactory",
    "UnitOfWorkFactory",
    "APIHandlerFactory",
    "SQLEngineFactory",
]
