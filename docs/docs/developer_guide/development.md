# Development Guide

This guide covers setting up a development environment, understanding the codebase structure, and contributing to the Open Host Factory Plugin.

## Development Environment Setup

### Prerequisites

- **Python 3.8+**: Required for the application
- **Git**: For version control
- **AWS CLI**: For AWS provider functionality (optional)
- **Docker**: For containerized development (optional)

### Local Development Setup

1. **Clone the Repository**
   ```bash
   git clone <repository-url>
   cd open-hostfactory-plugin
   ```

2. **Create Virtual Environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install Dependencies**
   ```bash
   # Install runtime dependencies
   pip install -r requirements.txt

   # Install development dependencies
   pip install -r requirements-dev.txt
   ```

4. **Configure Environment**
   ```bash
   # Copy example configuration
   cp config/config.example.json config/config.json

   # Edit configuration for your environment
   vim config/config.json
   ```

5. **Initialize Database**
   ```bash
   # Create data directory
   mkdir -p data

   # Initialize database (if using SQLite)
   python -m src.infrastructure.persistence.database.init_db
   ```

6. **Run Tests**
   ```bash
   # Run all tests
   pytest

   # Run with coverage
   pytest --cov=src --cov-report=html
   ```

7. **Start Development Server**
   ```bash
   # Run in development mode
   python -m src.bootstrap --config config/config.json --log-level DEBUG
   ```

### Development Configuration

Create a `config/dev-config.json` file for development:

```json
{
  "aws": {
    "region": "us-east-1",
    "profile": "default"
  },
  "logging": {
    "level": "DEBUG",
    "file_path": "logs/dev.log",
    "console_enabled": true,
    "format": "detailed"
  },
  "database": {
    "type": "sqlite",
    "name": "dev_database.db"
  },
  "REPOSITORY_CONFIG": {
    "type": "json",
    "json": {
      "storage_type": "single_file",
      "base_path": "data/dev",
      "filenames": {
        "single_file": "dev_database.json"
      }
    }
  },
  "development": {
    "auto_reload": true,
    "debug_mode": true,
    "mock_providers": false
  }
}
```

## Project Structure

### High-Level Architecture

The project follows Domain-Driven Design (DDD) with clean architecture principles:

```
open-hostfactory-plugin/
+--- src/                     # Source code
|   +--- domain/              # Domain layer (business logic)
|   +--- application/         # Application layer (use cases)
|   +--- infrastructure/      # Infrastructure layer (technical concerns)
|   +--- providers/           # Provider implementations
|   +--- api/                 # API layer
+--- tests/                   # Test suite
+--- config/                  # Configuration files
+--- scripts/                 # Shell scripts
+--- docs/                    # Documentation
+--- requirements*.txt        # Dependencies
```

### Domain Layer (`src/domain/`)

Contains pure business logic with no external dependencies:

```
domain/
+--- base/                    # Shared kernel
|   +--- entity.py           # Base entities and aggregates
|   +--- value_objects.py    # Common value objects
|   +--- events.py           # Domain events
|   +--- exceptions.py       # Domain exceptions
|   +--- repository.py       # Repository interfaces
+--- template/               # Template bounded context
+--- machine/                # Machine bounded context
+--- request/                # Request bounded context
```

**Key Principles:**
- No dependencies on infrastructure or external libraries
- Rich domain models with business logic
- Domain events for state changes
- Value objects for data integrity

### Application Layer (`src/application/`)

Orchestrates domain operations and coordinates with infrastructure:

```
application/
+--- base/                   # Base application components
+--- dto/                    # Data transfer objects
+--- interfaces/             # Application interfaces
+--- commands/               # Command handlers (CQRS)
+--- queries/                # Query handlers (CQRS)
+--- template/               # Template use cases
+--- machine/                # Machine use cases
+--- request/                # Request use cases
```

**Key Principles:**
- Thin orchestration layer
- CQRS pattern for complex operations
- Service pattern for simple CRUD operations
- DTO objects for data transfer

### Infrastructure Layer (`src/infrastructure/`)

Implements technical concerns and external integrations:

```
infrastructure/
+--- interfaces/             # Technical interfaces
+--- ports/                  # External system ports
+--- persistence/            # Data persistence
+--- events/                 # Event infrastructure
+--- logging/                # Logging utilities
+--- config/                 # Configuration management
+--- di/                     # Dependency injection
```

**Key Principles:**
- Implements domain interfaces
- Handles external system integration
- Provides technical services
- Configurable implementations

### Provider Layer (`src/providers/`)

Cloud provider-specific implementations:

```
providers/
+--- aws/                    # AWS provider
    +--- domain/             # AWS domain extensions
    +--- application/        # AWS application services
    +--- infrastructure/     # AWS infrastructure
    +--- managers/           # AWS resource managers
```

**Key Principles:**
- Provider-agnostic domain layer
- Cloud-specific implementations
- Extensible for multiple providers
- Clean separation of concerns

## Development Workflow

### Feature Development

1. **Create Feature Branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Write Tests First (TDD)**
   ```bash
   # Create test file
   touch tests/test_your_feature.py

   # Write failing tests
   pytest tests/test_your_feature.py -v
   ```

3. **Implement Feature**
   - Start with domain layer (business logic)
   - Add application layer (use cases)
   - Implement infrastructure layer (technical details)
   - Update provider layer if needed

4. **Run Tests**
   ```bash
   # Run specific tests
   pytest tests/test_your_feature.py -v

   # Run all tests
   pytest

   # Check coverage
   pytest --cov=src --cov-report=term-missing
   ```

5. **Update Documentation**
   ```bash
   # Update relevant documentation
   vim docs/docs/user_guide/your-feature.md

   # Build documentation
   cd docs && mkdocs serve
   ```

6. **Commit Changes**
   ```bash
   git add .
   git commit -m "feat: add your feature description"
   ```

### Code Style and Standards

#### Python Code Style

Follow PEP 8 with these specific guidelines:

```python
# Use type hints with appropriate flexibility
def create_request(template_id: str, machine_count: int) -> str:
    """Create a new machine request."""
    pass

# Use flexible typing for CLI argument handling
from typing import Any

def convert_cli_args_to_hostfactory_input(self, operation: str, args: Any) -> Dict[str, Any]:
    """Convert CLI arguments to HostFactory JSON input format.

    Uses Any type for args parameter to support different argument sources
    including argparse.Namespace, dict, or other argument containers.
    """
    pass

# Use dataclasses for DTOs
@dataclass
class RequestDto:
    request_id: str
    template_id: str
    machine_count: int

# Use enums for constants
class RequestStatus(Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

# Use dependency injection
class RequestService:
    def __init__(self, repository: RequestRepository):
        self._repository = repository
```

#### Naming Conventions

- **Classes**: PascalCase (`RequestService`, `MachineRepository`)
- **Functions/Methods**: snake_case (`create_request`, `get_machine_status`)
- **Variables**: snake_case (`request_id`, `machine_count`)
- **Constants**: UPPER_SNAKE_CASE (`MAX_MACHINE_COUNT`, `DEFAULT_TIMEOUT`)
- **Private Members**: Leading underscore (`_repository`, `_logger`)

#### Documentation Standards

```python
class RequestService:
    """Service for managing machine requests.

    This service provides high-level operations for creating,
    updating, and querying machine requests.
    """

    def create_request(self, template_id: str, machine_count: int) -> str:
        """Create a new machine request.

        Args:
            template_id: ID of the template to use
            machine_count: Number of machines to request

        Returns:
            The ID of the created request

        Raises:
            TemplateNotFoundError: If template doesn't exist
            ValidationError: If parameters are invalid
        """
        pass
```

### Testing Guidelines

#### Test Structure

```python
class TestRequestService:
    """Test suite for RequestService."""

    @pytest.fixture
    def service(self):
        """Create service instance for testing."""
        repository = Mock(spec=RequestRepository)
        return RequestService(repository)

    def test_create_request_success(self, service):
        """Test successful request creation."""
        # Arrange
        template_id = "template-1"
        machine_count = 2

        # Act
        result = service.create_request(template_id, machine_count)

        # Assert
        assert result is not None
        service._repository.save.assert_called_once()

    def test_create_request_invalid_template(self, service):
        """Test request creation with invalid template."""
        # Arrange
        service._repository.get_template.return_value = None

        # Act & Assert
        with pytest.raises(TemplateNotFoundError):
            service.create_request("invalid-template", 2)
```

#### Test Categories

1. **Unit Tests**: Test individual components in isolation
2. **Integration Tests**: Test component interactions
3. **End-to-End Tests**: Test complete workflows
4. **Performance Tests**: Test performance characteristics

#### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_request_service.py

# Run tests with specific marker
pytest -m unit

# Run tests with coverage
pytest --cov=src --cov-report=html

# Run tests in parallel
pytest -n auto
```

### Debugging

#### Logging

The application uses structured logging:

```python
from src.infrastructure.logging import get_logger

logger = get_logger(__name__)

def create_request(template_id: str, machine_count: int) -> str:
    logger.info("Creating request", extra={
        "template_id": template_id,
        "machine_count": machine_count
    })

    try:
        # Business logic
        request_id = "req-123"

        logger.info("Request created successfully", extra={
            "request_id": request_id
        })

        return request_id

    except Exception as e:
        logger.error("Failed to create request", extra={
            "template_id": template_id,
            "machine_count": machine_count,
            "error": str(e)
        })
        raise
```

#### Debug Configuration

Enable debug mode in configuration:

```json
{
  "logging": {
    "level": "DEBUG",
    "console_enabled": true,
    "format": "detailed"
  },
  "development": {
    "debug_mode": true,
    "auto_reload": true
  }
}
```

#### Debugging Tools

```bash
# Enable debug logging
export HF_LOG_LEVEL=DEBUG

# Run with Python debugger
python -m pdb -m src.bootstrap

# Use IPython for interactive debugging
pip install ipython
ipython -m src.bootstrap
```

### Performance Optimization

#### Profiling

```bash
# Profile application startup
python -m cProfile -o profile.stats -m src.bootstrap

# Analyze profile results
python -c "import pstats; pstats.Stats('profile.stats').sort_stats('cumulative').print_stats(20)"

# Memory profiling
pip install memory-profiler
python -m memory_profiler src/bootstrap.py
```

#### Performance Guidelines

1. **Database Queries**: Use appropriate indexes and query optimization
2. **Caching**: Implement caching for frequently accessed data
3. **Async Operations**: Use async/await for I/O operations
4. **Batch Processing**: Process multiple items together when possible
5. **Connection Pooling**: Reuse connections to external services

### Security Considerations

#### Secure Coding Practices

```python
# Input validation
def create_request(template_id: str, machine_count: int) -> str:
    if not template_id or not template_id.strip():
        raise ValidationError("template_id is required")

    if machine_count <= 0 or machine_count > 1000:
        raise ValidationError("machine_count must be between 1 and 1000")

# Secure credential handling
import os
from src.infrastructure.config import get_config

def get_aws_credentials():
    # Never hardcode credentials
    config = get_config()
    return {
        'access_key': os.environ.get('AWS_ACCESS_KEY_ID'),
        'secret_key': os.environ.get('AWS_SECRET_ACCESS_KEY'),
        'region': config.aws.region
    }

# SQL injection prevention (using parameterized queries)
def get_requests_by_status(status: str) -> List[Request]:
    query = "SELECT * FROM requests WHERE status = ?"
    return database.execute(query, (status,))
```

#### Security Checklist

- [ ] Input validation on all user inputs
- [ ] Parameterized database queries
- [ ] Secure credential storage
- [ ] Appropriate error handling (don't leak sensitive info)
- [ ] Access control and authorization
- [ ] Secure communication (HTTPS, TLS)
- [ ] Regular dependency updates

## Contributing

### Pull Request Process

1. **Fork the Repository**
   ```bash
   git clone <your-fork-url>
   cd open-hostfactory-plugin
   ```

2. **Create Feature Branch**
   ```bash
   git checkout -b feature/your-feature
   ```

3. **Make Changes**
   - Follow coding standards
   - Write tests
   - Update documentation

4. **Test Changes**
   ```bash
   pytest
   flake8 src/
   mypy src/
   ```

5. **Commit Changes**
   ```bash
   git add .
   git commit -m "feat: add your feature"
   ```

6. **Push and Create PR**
   ```bash
   git push origin feature/your-feature
   # Create pull request on GitHub
   ```

### Commit Message Format

Use conventional commits format:

```
type(scope): description

[optional body]

[optional footer]
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes
- `refactor`: Code refactoring
- `test`: Test changes
- `chore`: Build/tooling changes

**Examples:**
```
feat(request): add request priority support
fix(aws): handle EC2 throttling errors
docs(api): update API documentation
refactor(domain): simplify request aggregate
```

### Code Review Guidelines

#### For Authors
- Keep PRs small and focused
- Write clear commit messages
- Include tests for new functionality
- Update documentation
- Respond to feedback promptly

#### For Reviewers
- Review for correctness and design
- Check test coverage
- Verify documentation updates
- Consider performance implications
- Be constructive in feedback

## Troubleshooting

### Common Development Issues

#### Import Errors
```bash
# Check Python path
python -c "import sys; print('\n'.join(sys.path))"

# Install in development mode
pip install -e .

# Check for circular imports
python -m src.bootstrap --check-imports
```

#### Test Failures
```bash
# Run tests with verbose output
pytest -v

# Run specific failing test
pytest tests/test_specific.py::test_method -v

# Check test dependencies
pip list | grep pytest
```

#### Configuration Issues
```bash
# Validate configuration
python -m src.infrastructure.config.validate

# Check environment variables
env | grep HF_

# Test configuration loading
python -c "from src.infrastructure.config import get_config; print(get_config())"
```

#### Database Issues
```bash
# Check database connectivity
python -m src.infrastructure.persistence.database.check_connection

# Reset database
rm data/database.db
python -m src.infrastructure.persistence.database.init_db

# Check database schema
python -m src.infrastructure.persistence.database.show_schema
```

### Getting Help

1. **Check Documentation**: Review relevant documentation sections
2. **Search Issues**: Look for similar issues in the repository
3. **Ask Questions**: Create an issue with the "question" label
4. **Join Discussions**: Participate in repository discussions

## Next Steps

- **[Architecture](architecture.md)**: Understand the system architecture
- **[CQRS](cqrs.md)**: Learn about command and query patterns
- **[Events](events.md)**: Understand the event system
- **[Providers](providers.md)**: Learn about provider integration
- **[API Reference](../api/)**: Explore the API documentation
