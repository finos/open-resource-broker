# Source Code Architecture

This directory contains the complete source code for the Open Resource Broker, implementing Clean Architecture with Domain-Driven Design (DDD) and CQRS.

## Layer diagram

```
src/orb/
├── domain/          # Core business logic — no dependencies on other layers
├── application/     # Use cases, CQRS handlers, orchestrators, DTOs
├── infrastructure/  # Repositories, DI container, external integrations
├── interface/       # CLI handlers and CLI-facing adapters
├── api/             # FastAPI routers (REST interface)
├── cli/             # Click-based CLI entry points
├── config/          # Pydantic config schemas and loading
├── mcp/             # MCP server interface
├── providers/       # AWS provider implementation (commands, queries, config)
└── sdk/             # Public SDK surface for external callers
```

## Layer responsibilities

### `domain/`
- Aggregates, Value Objects, Domain Events, Port interfaces
- No imports from any other layer

### `application/`
- CQRS command/query handlers, orchestrators, DTOs
- Imports domain only
- Orchestrators in `services/orchestration/` dispatch to `CommandBusPort`/`QueryBusPort`

### `infrastructure/`
- Concrete port implementations: repositories, buses, scheduler adapters, DI container
- Imports domain and application

### `interface/`
- CLI command handlers; thin wrappers that call orchestrators
- Imports all layers

### `api/`
- FastAPI routers; thin wrappers that call orchestrators and format via `SchedulerPort`
- Imports application and infrastructure

### `providers/`
- AWS-specific command handlers, query handlers, and configuration
- Registered into the DI container at bootstrap

## Import conventions

```python
# Correct — use the orb package prefix
from orb.domain.base.exceptions import EntityNotFoundError
from orb.application.services.orchestration.dtos import ListTemplatesInput
from orb.infrastructure.di.container import get_container

# Wrong — never use src.*
from src.domain...  # invalid
```

## Key patterns

### CQRS handlers
```python
@command_handler(MyCommand)
class MyCommandHandler(BaseCommandHandler[MyCommand, MyResponse]):
    async def execute_command(self, command: MyCommand) -> MyResponse: ...

@query_handler(MyQuery)
class MyQueryHandler(BaseQueryHandler[MyQuery, MyResult]):
    async def execute_query(self, query: MyQuery) -> MyResult: ...
```

### Dependency injection
```python
@injectable
class MyService:
    def __init__(self, repo: MyRepositoryPort) -> None:
        self._repo = repo
```

## Testing

- `tests/unit/` — isolated component tests
- `tests/integration/` — layer interaction tests
- `tests/contract/` — orchestrator contract tests
- `tests/onaws/` — real AWS tests (require `--run-aws` flag)
