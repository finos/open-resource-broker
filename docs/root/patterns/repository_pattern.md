# Repository Pattern Implementation

This document describes the implementation of the Repository pattern in the Open Host Factory Plugin, which provides a clean abstraction layer for data access operations while supporting multiple storage backends.

## Repository Pattern Overview

The Repository pattern provides:

- **Data access abstraction**: Clean separation between business logic and data persistence
- **Multiple storage backends**: Support for different storage technologies
- **Consistent interface**: Uniform data access across different storage types
- **Testability**: Easy mocking and testing of data operations

## Repository Interface Definitions

Repository interfaces are defined in the domain layer and implemented in the infrastructure layer.

### Base Repository Interface

```python
# src/domain/base/repository.py
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, TypeVar, Generic

T = TypeVar('T')

class Repository(ABC, Generic[T]):
    """Base repository interface for all domain entities."""

    @abstractmethod
    async def get_by_id(self, entity_id: str) -> Optional[T]:
        """Retrieve entity by ID."""
        pass

    @abstractmethod
    async def get_all(self, 
                     filters: Optional[Dict[str, Any]] = None,
                     limit: Optional[int] = None,
                     offset: Optional[int] = None) -> List[T]:
        """Retrieve all entities with optional filtering."""
        pass

    @abstractmethod
    async def save(self, entity: T) -> None:
        """Save entity."""
        pass

    @abstractmethod
    async def delete(self, entity_id: str) -> bool:
        """Delete entity by ID."""
        pass

    @abstractmethod
    async def exists(self, entity_id: str) -> bool:
        """Check if entity exists."""
        pass
```

### Template Repository Interface

```python
# src/domain/template/repository.py
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from .aggregate import Template

class TemplateRepository(ABC):
    """Repository interface for template entities."""

    @abstractmethod
    async def get_all(self, 
                     filters: Optional[Dict[str, Any]] = None,
                     limit: Optional[int] = None,
                     offset: Optional[int] = None) -> List[Template]:
        """Retrieve all templates with optional filtering."""
        pass

    @abstractmethod
    async def get_by_id(self, template_id: str) -> Optional[Template]:
        """Retrieve template by ID."""
        pass

    @abstractmethod
    async def save(self, template: Template) -> None:
        """Save template."""
        pass

    @abstractmethod
    async def delete(self, template_id: str) -> bool:
        """Delete template by ID."""
        pass

    @abstractmethod
    async def find_by_attributes(self, attributes: Dict[str, Any]) -> List[Template]:
        """Find templates by specific attributes."""
        pass

    @abstractmethod
    async def get_by_provider_type(self, provider_type: str) -> List[Template]:
        """Get templates for specific provider type."""
        pass
```

### Request Repository Interface

```python
# src/domain/request/repository.py
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from .aggregate import Request
from .value_objects import RequestStatus

class RequestRepository(ABC):
    """Repository interface for request entities."""

    @abstractmethod
    async def get_all(self, 
                     filters: Optional[Dict[str, Any]] = None,
                     limit: Optional[int] = None,
                     offset: Optional[int] = None) -> List[Request]:
        """Retrieve all requests with optional filtering."""
        pass

    @abstractmethod
    async def get_by_id(self, request_id: str) -> Optional[Request]:
        """Retrieve request by ID."""
        pass

    @abstractmethod
    async def save(self, request: Request) -> None:
        """Save request."""
        pass

    @abstractmethod
    async def delete(self, request_id: str) -> bool:
        """Delete request by ID."""
        pass

    @abstractmethod
    async def get_by_status(self, status: RequestStatus) -> List[Request]:
        """Get requests by status."""
        pass

    @abstractmethod
    async def get_active_requests(self) -> List[Request]:
        """Get all active (non-completed) requests."""
        pass

    @abstractmethod
    async def update_status(self, request_id: str, status: RequestStatus) -> bool:
        """Update request status."""
        pass
```

### Machine Repository Interface

```python
# src/domain/machine/repository.py
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from .aggregate import Machine
from .machine_status import MachineStatus

class MachineRepository(ABC):
    """Repository interface for machine entities."""

    @abstractmethod
    async def get_all(self, 
                     filters: Optional[Dict[str, Any]] = None,
                     limit: Optional[int] = None,
                     offset: Optional[int] = None) -> List[Machine]:
        """Retrieve all machines with optional filtering."""
        pass

    @abstractmethod
    async def get_by_id(self, machine_id: str) -> Optional[Machine]:
        """Retrieve machine by ID."""
        pass

    @abstractmethod
    async def save(self, machine: Machine) -> None:
        """Save machine."""
        pass

    @abstractmethod
    async def delete(self, machine_id: str) -> bool:
        """Delete machine by ID."""
        pass

    @abstractmethod
    async def get_by_request_id(self, request_id: str) -> List[Machine]:
        """Get machines associated with a request."""
        pass

    @abstractmethod
    async def get_by_status(self, status: MachineStatus) -> List[Machine]:
        """Get machines by status."""
        pass

    @abstractmethod
    async def update_status(self, machine_id: str, status: MachineStatus) -> bool:
        """Update machine status."""
        pass

    @abstractmethod
    async def get_by_instance_ids(self, instance_ids: List[str]) -> List[Machine]:
        """Get machines by cloud provider instance IDs."""
        pass
```

## DynamoDB Repository Implementations

DynamoDB implementations provide scalable, managed NoSQL storage.

### DynamoDB Template Repository

```python
# src/infrastructure/persistence/dynamodb/template_repository.py
from typing import List, Optional, Dict, Any
import boto3
from boto3.dynamodb.conditions import Key, Attr
from src.domain.template.repository import TemplateRepository
from src.domain.template.aggregate import Template
from src.domain.base.ports import LoggingPort

class DynamoDBTemplateRepository(TemplateRepository):
    """DynamoDB implementation of template repository."""

    def __init__(self, 
                 table_name: str,
                 region: str,
                 profile: Optional[str] = None,
                 logger: LoggingPort = None):
        self._table_name = table_name
        self._region = region
        self._profile = profile
        self._logger = logger

        # Initialize DynamoDB resources
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        self._dynamodb = session.resource('dynamodb', region_name=region)
        self._table = self._dynamodb.Table(table_name)

    async def get_all(self, 
                     filters: Optional[Dict[str, Any]] = None,
                     limit: Optional[int] = None,
                     offset: Optional[int] = None) -> List[Template]:
        """Retrieve all templates from DynamoDB."""
        self._logger.info("Retrieving all templates from DynamoDB")

        try:
            # Build scan parameters
            scan_params = {}

            if limit:
                scan_params['Limit'] = limit

            # Apply filters
            if filters:
                filter_expression = self._build_filter_expression(filters)
                if filter_expression:
                    scan_params['FilterExpression'] = filter_expression

            # Handle pagination
            if offset:
                # DynamoDB uses LastEvaluatedKey for pagination
                # This is a simplified implementation
                scan_params['ExclusiveStartKey'] = {'template_id': str(offset)}

            # Perform scan
            response = self._table.scan(**scan_params)

            # Convert items to domain objects
            templates = []
            for item in response.get('Items', []):
                template = self._item_to_template(item)
                templates.append(template)

            self._logger.info(f"Retrieved {len(templates)} templates")
            return templates

        except Exception as e:
            self._logger.error(f"Error retrieving templates: {e}")
            raise

    async def get_by_id(self, template_id: str) -> Optional[Template]:
        """Retrieve template by ID from DynamoDB."""
        self._logger.info(f"Retrieving template: {template_id}")

        try:
            response = self._table.get_item(
                Key={'template_id': template_id}
            )

            if 'Item' not in response:
                self._logger.info(f"Template not found: {template_id}")
                return None

            template = self._item_to_template(response['Item'])
            self._logger.info(f"Retrieved template: {template_id}")
            return template

        except Exception as e:
            self._logger.error(f"Error retrieving template {template_id}: {e}")
            raise

    async def save(self, template: Template) -> None:
        """Save template to DynamoDB."""
        self._logger.info(f"Saving template: {template.template_id}")

        try:
            item = self._template_to_item(template)

            self._table.put_item(Item=item)

            self._logger.info(f"Saved template: {template.template_id}")

        except Exception as e:
            self._logger.error(f"Error saving template {template.template_id}: {e}")
            raise

    async def delete(self, template_id: str) -> bool:
        """Delete template from DynamoDB."""
        self._logger.info(f"Deleting template: {template_id}")

        try:
            response = self._table.delete_item(
                Key={'template_id': template_id},
                ReturnValues='ALL_OLD'
            )

            deleted = 'Attributes' in response

            if deleted:
                self._logger.info(f"Deleted template: {template_id}")
            else:
                self._logger.warning(f"Template not found for deletion: {template_id}")

            return deleted

        except Exception as e:
            self._logger.error(f"Error deleting template {template_id}: {e}")
            raise

    async def find_by_attributes(self, attributes: Dict[str, Any]) -> List[Template]:
        """Find templates by specific attributes."""
        self._logger.info(f"Finding templates by attributes: {attributes}")

        try:
            # Build filter expression for attributes
            filter_expressions = []
            for key, value in attributes.items():
                filter_expressions.append(Attr(f'attributes.{key}').eq(value))

            if not filter_expressions:
                return []

            # Combine filter expressions
            filter_expression = filter_expressions[0]
            for expr in filter_expressions[1:]:
                filter_expression = filter_expression & expr

            # Perform scan with filter
            response = self._table.scan(FilterExpression=filter_expression)

            # Convert to domain objects
            templates = []
            for item in response.get('Items', []):
                template = self._item_to_template(item)
                templates.append(template)

            self._logger.info(f"Found {len(templates)} templates matching attributes")
            return templates

        except Exception as e:
            self._logger.error(f"Error finding templates by attributes: {e}")
            raise

    async def get_by_provider_type(self, provider_type: str) -> List[Template]:
        """Get templates for specific provider type."""
        return await self.find_by_attributes({'provider_type': provider_type})

    def _item_to_template(self, item: Dict[str, Any]) -> Template:
        """Convert DynamoDB item to Template domain object."""
        return Template(
            template_id=item['template_id'],
            max_number=int(item['max_number']),
            attributes=item.get('attributes', {})
        )

    def _template_to_item(self, template: Template) -> Dict[str, Any]:
        """Convert Template domain object to DynamoDB item."""
        return {
            'template_id': template.template_id,
            'max_number': template.max_number,
            'attributes': template.attributes
        }

    def _build_filter_expression(self, filters: Dict[str, Any]):
        """Build DynamoDB filter expression from filters."""
        if not filters:
            return None

        filter_expressions = []
        for key, value in filters.items():
            if key == 'max_number_gte':
                filter_expressions.append(Attr('max_number').gte(value))
            elif key == 'max_number_lte':
                filter_expressions.append(Attr('max_number').lte(value))
            else:
                filter_expressions.append(Attr(key).eq(value))

        if not filter_expressions:
            return None

        # Combine expressions with AND
        result = filter_expressions[0]
        for expr in filter_expressions[1:]:
            result = result & expr

        return result
```

## In-Memory Repository Implementations

In-memory implementations provide fast access for development and testing.

### In-Memory Template Repository

```python
# src/infrastructure/persistence/memory/template_repository.py
from typing import List, Optional, Dict, Any
from src.domain.template.repository import TemplateRepository
from src.domain.template.aggregate import Template
from src.domain.base.ports import LoggingPort

class InMemoryTemplateRepository(TemplateRepository):
    """In-memory implementation of template repository."""

    def __init__(self, logger: LoggingPort = None):
        self._templates: Dict[str, Template] = {}
        self._logger = logger

    async def get_all(self, 
                     filters: Optional[Dict[str, Any]] = None,
                     limit: Optional[int] = None,
                     offset: Optional[int] = None) -> List[Template]:
        """Retrieve all templates from memory."""
        self._logger.info("Retrieving all templates from memory")

        templates = list(self._templates.values())

        # Apply filters
        if filters:
            templates = self._apply_filters(templates, filters)

        # Apply pagination
        if offset:
            templates = templates[offset:]

        if limit:
            templates = templates[:limit]

        self._logger.info(f"Retrieved {len(templates)} templates")
        return templates

    async def get_by_id(self, template_id: str) -> Optional[Template]:
        """Retrieve template by ID from memory."""
        self._logger.info(f"Retrieving template: {template_id}")

        template = self._templates.get(template_id)

        if template:
            self._logger.info(f"Retrieved template: {template_id}")
        else:
            self._logger.info(f"Template not found: {template_id}")

        return template

    async def save(self, template: Template) -> None:
        """Save template to memory."""
        self._logger.info(f"Saving template: {template.template_id}")

        self._templates[template.template_id] = template

        self._logger.info(f"Saved template: {template.template_id}")

    async def delete(self, template_id: str) -> bool:
        """Delete template from memory."""
        self._logger.info(f"Deleting template: {template_id}")

        if template_id in self._templates:
            del self._templates[template_id]
            self._logger.info(f"Deleted template: {template_id}")
            return True
        else:
            self._logger.warning(f"Template not found for deletion: {template_id}")
            return False

    async def find_by_attributes(self, attributes: Dict[str, Any]) -> List[Template]:
        """Find templates by specific attributes."""
        self._logger.info(f"Finding templates by attributes: {attributes}")

        matching_templates = []

        for template in self._templates.values():
            if self._template_matches_attributes(template, attributes):
                matching_templates.append(template)

        self._logger.info(f"Found {len(matching_templates)} templates matching attributes")
        return matching_templates

    async def get_by_provider_type(self, provider_type: str) -> List[Template]:
        """Get templates for specific provider type."""
        return await self.find_by_attributes({'provider_type': provider_type})

    def _apply_filters(self, templates: List[Template], filters: Dict[str, Any]) -> List[Template]:
        """Apply filters to template list."""
        filtered_templates = []

        for template in templates:
            if self._template_matches_filters(template, filters):
                filtered_templates.append(template)

        return filtered_templates

    def _template_matches_filters(self, template: Template, filters: Dict[str, Any]) -> bool:
        """Check if template matches filter criteria."""
        for key, value in filters.items():
            if key == 'max_number_gte':
                if template.max_number < value:
                    return False
            elif key == 'max_number_lte':
                if template.max_number > value:
                    return False
            elif hasattr(template, key):
                if getattr(template, key) != value:
                    return False

        return True

    def _template_matches_attributes(self, template: Template, attributes: Dict[str, Any]) -> bool:
        """Check if template matches attribute criteria."""
        for key, value in attributes.items():
            if key not in template.attributes or template.attributes[key] != value:
                return False

        return True
```

## Repository Registration and Usage

Repositories are registered in the DI container based on configuration.

### Repository Registration

```python
# src/infrastructure/di/repository_services.py
def register_repository_services(container: DIContainer) -> None:
    """Register repository services based on configuration."""

    config = container.get(ConfigurationPort)
    logger = container.get(LoggingPort)

    storage_type = config.get("storage.type", "memory")

    if storage_type == "dynamodb":
        _register_dynamodb_repositories(container, config, logger)
    elif storage_type == "memory":
        _register_memory_repositories(container, logger)
    else:
        raise ValueError(f"Unsupported storage type: {storage_type}")

def _register_dynamodb_repositories(container: DIContainer, 
                                   config: ConfigurationPort, 
                                   logger: LoggingPort) -> None:
    """Register DynamoDB repository implementations."""

    dynamodb_config = config.get_section("storage.dynamodb")

    # Register template repository
    container.register_singleton(
        TemplateRepository,
        lambda c: DynamoDBTemplateRepository(
            table_name=dynamodb_config.get("templates_table", "templates"),
            region=dynamodb_config.get("region", "us-east-1"),
            profile=dynamodb_config.get("profile"),
            logger=logger
        )
    )

    # Register request repository
    container.register_singleton(
        RequestRepository,
        lambda c: DynamoDBRequestRepository(
            table_name=dynamodb_config.get("requests_table", "requests"),
            region=dynamodb_config.get("region", "us-east-1"),
            profile=dynamodb_config.get("profile"),
            logger=logger
        )
    )

    # Register machine repository
    container.register_singleton(
        MachineRepository,
        lambda c: DynamoDBMachineRepository(
            table_name=dynamodb_config.get("machines_table", "machines"),
            region=dynamodb_config.get("region", "us-east-1"),
            profile=dynamodb_config.get("profile"),
            logger=logger
        )
    )

def _register_memory_repositories(container: DIContainer, logger: LoggingPort) -> None:
    """Register in-memory repository implementations."""

    container.register_singleton(
        TemplateRepository,
        lambda c: InMemoryTemplateRepository(logger=logger)
    )

    container.register_singleton(
        RequestRepository,
        lambda c: InMemoryRequestRepository(logger=logger)
    )

    container.register_singleton(
        MachineRepository,
        lambda c: InMemoryMachineRepository(logger=logger)
    )
```

### Repository Usage in Application Layer

```python
# src/application/commands/template_handlers.py
class GetTemplatesHandler:
    """Handler for retrieving templates."""

    def __init__(self, 
                 template_repo: TemplateRepository,
                 logger: LoggingPort):
        self._template_repo = template_repo
        self._logger = logger

    async def handle(self, query: GetTemplatesQuery) -> List[TemplateResponse]:
        """Handle template retrieval query."""
        self._logger.info("Handling get templates query")

        # Use repository to get templates
        templates = await self._template_repo.get_all(
            filters=query.filters,
            limit=query.limit,
            offset=query.offset
        )

        # Convert to response DTOs
        responses = [
            TemplateResponse.from_domain(template)
            for template in templates
        ]

        return responses
```

## Benefits of Repository Pattern Implementation

### Data Access Abstraction
- Clean separation between business logic and data persistence
- Consistent interface across different storage technologies
- Easy switching between storage backends

### Multiple Storage Support
- DynamoDB for scalable cloud storage
- In-memory for development and testing
- Easy addition of new storage backends

### Testability
- Easy mocking of repository interfaces
- In-memory implementations for fast testing
- Isolated testing of business logic

### Maintainability
- Clear data access patterns
- Centralized data access logic
- Easy modification of storage implementations

This Repository pattern implementation provides a solid foundation for data access operations while maintaining clean architecture principles and supporting multiple storage backends in the Open Host Factory Plugin.
