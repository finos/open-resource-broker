# Storage Registry Pattern Documentation

## Overview

The Storage Registry Pattern eliminates hard-coded storage conditionals and enables true OCP (Open/Closed Principle) compliance for storage types. This pattern maintains clean architecture separation while allowing easy addition of new storage types.

## Architecture

### Clean Separation of Concerns

```
+-----------------------+
|   Repository Layer  |  <- Creates repositories + injects strategies
+-----------------------+
|  Storage Registry   |  <- Only handles storage strategies
+-----------------------+
|   Storage Layer     |  <- Pure storage implementations
+-----------------------+
```

### Key Components

#### 1. Storage Registry
- **Purpose**: Registry for storage strategy factories
- **Responsibility**: Create storage strategies and unit of work instances
- **Location**: `src/infrastructure/registry/storage_registry.py`

```python
class StorageRegistry:
    def register_storage(self, storage_type: str, strategy_factory: Callable, 
                        config_factory: Callable, unit_of_work_factory: Callable)
    def create_strategy(self, storage_type: str, config: Any) -> BaseStorageStrategy
    def create_unit_of_work(self, storage_type: str, config: Any) -> UnitOfWork
```

#### 2. Repository Factory
- **Purpose**: Create repositories with injected storage strategies
- **Responsibility**: Use storage registry to get strategies, inject into repositories
- **Location**: `src/infrastructure/utilities/factories/repository_factory.py`

```python
class RepositoryFactory:
    def create_request_repository(self) -> RequestRepository:
        storage_strategy = self.storage_registry.create_strategy(storage_type, config)
        return RequestRepository(storage_strategy)  # Clean injection
```

#### 3. Storage Registration Modules
- **Purpose**: Register storage types with the registry
- **Responsibility**: Provide factory functions for each storage type
- **Locations**: 
  - `src/infrastructure/persistence/json/registration.py`
  - `src/infrastructure/persistence/sql/registration.py`
  - `src/providers/aws/persistence/dynamodb/registration.py`

## Usage

### Adding a New Storage Type

#### Create Storage Strategy
```python
# src/infrastructure/persistence/redis/strategy.py
class RedisStorageStrategy(BaseStorageStrategy):
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
    # ... implement storage methods
```

#### Create Registration Module
```python
# src/infrastructure/persistence/redis/registration.py
def create_redis_strategy(config: Any) -> RedisStorageStrategy:
    return RedisStorageStrategy(config.redis_strategy.connection_string)

def create_redis_config(data: Dict[str, Any]) -> Any:
    return RedisStrategyConfig(**data)

def create_redis_unit_of_work(config: Any) -> Any:
    return RedisUnitOfWork(config)

def register_redis_storage() -> None:
    registry = get_storage_registry()
    registry.register_storage(
        storage_type="redis",
        strategy_factory=create_redis_strategy,
        config_factory=create_redis_config,
        unit_of_work_factory=create_redis_unit_of_work
    )
```

#### Register in Central Module
```python
# src/infrastructure/persistence/registration.py
def register_all_storage_types() -> None:
    # ... existing registrations

    # Add Redis registration
    try:
        from src.infrastructure.persistence.redis.registration import register_redis_storage
        register_redis_storage()
        registered_types.append("redis")
    except Exception as e:
        failed_types.append(("redis", str(e)))
```

#### Update Configuration Schema
```python
# src/config/schemas/storage_schema.py
class RedisStrategyConfig(BaseModel):
    connection_string: str
    database: int = 0
    timeout: int = 30
```

**That's it!** No existing code needs modification. The new storage type is automatically available throughout the application.

## Benefits

### 1. OCP Compliance
- [[]] **Open for Extension**: Easy to add new storage types
- [[]] **Closed for Modification**: No existing code changes needed

### 2. Clean Architecture
- [[]] **Separation of Concerns**: Storage registry only handles storage
- [[]] **Dependency Inversion**: Repositories depend on abstractions
- [[]] **Single Responsibility**: Each component has one clear purpose

### 3. Maintainability
- [[]] **Centralized Registration**: Single point for storage type management
- [[]] **Consistent Patterns**: All storage types follow same pattern
- [[]] **Easy Testing**: Clean interfaces for mocking

### 4. Flexibility
- [[]] **Runtime Configuration**: Storage type determined by configuration
- [[]] **Multiple Storage Types**: Can support different storage for different entities
- [[]] **Easy Migration**: Simple to migrate between storage types

## Implementation Details

### Repository Creation Flow
```
1. RepositoryFactory.create_request_repository()
2. -> storage_type = config_manager.get_storage_strategy()
3. -> storage_strategy = storage_registry.create_strategy(storage_type, config)
4. -> return RequestRepository(storage_strategy)
```

### DI Container Integration
```python
# src/infrastructure/di/infrastructure_services.py
def _register_repository_services(container: DIContainer) -> None:
    # Ensure all storage types are registered
    register_all_storage_types()

    # Register repository factory
    container.register_singleton(RepositoryFactory, ...)

    # Register repositories using the factory
    container.register_singleton(RequestRepositoryInterface, 
                                lambda c: c.get(RepositoryFactory).create_request_repository())
```

### Configuration Support
```json
{
  "storage": {
    "strategy": "json",
    "json_strategy": {
      "base_path": "data",
      "storage_type": "single_file"
    }
  }
}
```

## Testing

### Unit Tests
- Storage Registry: Test registration and creation methods
- Repository Factory: Test strategy injection
- Registration Modules: Test factory functions

### Integration Tests
- End-to-end repository creation
- DI container integration
- Configuration loading

### Example Test
```python
def test_repository_creation_with_storage_registry():
    registry = get_storage_registry()
    registry.register_storage("test", create_test_strategy, create_test_config)

    factory = RepositoryFactory(config_manager)
    repository = factory.create_request_repository()

    assert isinstance(repository, RequestRepository)
    assert isinstance(repository.storage_strategy, TestStorageStrategy)
```

## Migration Guide

### From Hard-coded Conditionals
```python
# [[]] BEFORE (Hard-coded)
if storage_type == "json":
    return JSONRepository()
elif storage_type == "sql":
    return SQLRepository()

# [[]] AFTER (Registry-based)
storage_strategy = registry.create_strategy(storage_type, config)
return Repository(storage_strategy)
```

### From Storage-Specific Repositories
```python
# [[]] BEFORE (Storage-specific repositories)
JSONRequestRepository(config)
SQLRequestRepository(config)

# [[]] AFTER (Generic repository with strategy injection)
storage_strategy = registry.create_strategy(storage_type, config)
RequestRepository(storage_strategy)
```

## Best Practices

### 1. Keep Storage Registry Pure
- Only handle storage strategies and unit of work
- No repository knowledge in storage layer
- Clean separation of concerns

### 2. Use Factory Pattern
- Repository Factory creates repositories
- Storage Registry creates strategies
- Clear responsibility boundaries

### 3. Consistent Registration
- All storage types follow same registration pattern
- Include unit of work factory
- Proper error handling

### 4. Configuration Validation
- Validate storage configuration
- Provide meaningful error messages
- Support configuration migration

## Troubleshooting

### Common Issues

#### 1. Storage Type Not Registered
```
UnsupportedStorageError: Storage type 'redis' is not registered
```
**Solution**: Ensure storage type is registered in `register_all_storage_types()`

#### 2. Unit of Work Factory Missing
```
UnsupportedStorageError: Unit of work factory not registered for storage type 'redis'
```
**Solution**: Add `unit_of_work_factory` parameter to `register_storage()`

#### 3. Configuration Schema Missing
```
ValidationError: Unknown storage strategy: redis
```
**Solution**: Add storage configuration schema to `storage_schema.py`

### Debugging Tips

1. **Check Registration**: Verify storage type is in `registry.get_registered_storage_types()`
2. **Test Factory Functions**: Ensure factory functions work independently
3. **Validate Configuration**: Check configuration matches expected schema
4. **Review Logs**: Storage registry logs all registration and creation activities

## Future Enhancements

### Planned Features
- **Storage Health Checks**: Monitor storage availability
- **Storage Metrics**: Track storage performance
- **Storage Migration**: Tools for migrating between storage types
- **Storage Pooling**: Connection pooling for database storage types

### Extension Points
- **Custom Storage Strategies**: Support for custom storage implementations
- **Storage Middleware**: Caching, compression, encryption layers
- **Storage Routing**: Route different entities to different storage types
- **Storage Replication**: Multi-storage replication support
