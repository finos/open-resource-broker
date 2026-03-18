"""Infrastructure utilities - common utilities and factories."""

# Import common utilities
# Export abstract interface from canonical location
from orb.domain.base import UnitOfWorkFactory
from orb.infrastructure.utilities.common.collections import (
    filter_dict,
    group_by,
    transform_list,
    validate_collection,
)
from orb.infrastructure.utilities.common.date_utils import (
    format_datetime,
    get_current_timestamp,
    parse_datetime,
)
from orb.infrastructure.utilities.common.file_utils import (
    ensure_directory_exists,
    read_json_file,
    write_json_file,
)
from orb.infrastructure.utilities.common.resource_naming import (
    get_resource_prefix,
)
from orb.infrastructure.utilities.common.serialization import (
    deserialize_enum,
    process_value_objects,
    serialize_enum,
)
from orb.infrastructure.utilities.common.string_utils import (
    extract_provider_type,
)

# Import factories (removed legacy ProviderFactory)
from orb.infrastructure.utilities.factories.repository_factory import RepositoryFactory
from orb.infrastructure.utilities.factories.sql_engine_factory import SQLEngineFactory

__all__: list[str] = [
    # Factories (legacy ProviderFactory removed)
    "RepositoryFactory",
    "SQLEngineFactory",
    "UnitOfWorkFactory",
    # String utilities
    "extract_provider_type",
    "deserialize_enum",
    # File utilities
    "ensure_directory_exists",
    # Collection utilities
    "filter_dict",
    # Date utilities
    "format_datetime",
    "get_current_timestamp",
    # Resource naming
    "get_resource_prefix",
    "group_by",
    "parse_datetime",
    "process_value_objects",
    "read_json_file",
    # Serialization
    "serialize_enum",
    "transform_list",
    "validate_collection",
    "write_json_file",
]
