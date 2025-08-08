# Testing Guide for Open Host Factory Plugin

This document provides comprehensive information about the testing ecosystem for the Open Host Factory Plugin.

## Table of Contents

- [Overview](#overview)
- [Test Structure](#test-structure)
- [Test Categories](#test-categories)
- [Running Tests](#running-tests)
- [Test Configuration](#test-configuration)
- [Writing Tests](#writing-tests)
- [Continuous Integration](#continuous-integration)
- [Coverage Reports](#coverage-reports)
- [Troubleshooting](#troubleshooting)

## Overview

The Open Host Factory Plugin uses a comprehensive testing strategy that includes:

- **Unit Tests**: Fast, isolated tests for individual components
- **Integration Tests**: Tests for component interactions and workflows
- **End-to-End Tests**: Full application workflow tests
- **Performance Tests**: Load and performance testing
- **Security Tests**: Security vulnerability and compliance testing

## Test Structure

```
tests/
|-- __init__.py
|-- conftest.py                 # Global test configuration and fixtures
|-- utilities/
|   `-- reset_singletons.py    # Test utilities
|-- unit/                       # Unit tests
|   |-- __init__.py
|   |-- domain/                 # Domain layer tests
|   |-- application/            # Application layer tests
|   |-- infrastructure/         # Infrastructure layer tests
|   `-- providers/              # Provider layer tests
|-- integration/                # Integration tests
|   |-- __init__.py
|   `-- test_full_workflow.py   # Full workflow integration tests
`-- e2e/                        # End-to-end tests
    |-- __init__.py
    `-- test_api_endpoints.py    # API endpoint E2E tests
```

## Test Categories

### Unit Tests (`@pytest.mark.unit`)

Fast, isolated tests that test individual components in isolation:

- Domain entities and value objects
- Application services and handlers
- Infrastructure components
- Provider implementations

**Characteristics:**
- Run in < 1 second each
- No external dependencies
- Extensive mocking
- High code coverage

### Integration Tests (`@pytest.mark.integration`)

Tests that verify component interactions:

- Application service workflows
- Repository operations
- Event handling
- Configuration management

**Characteristics:**
- Run in < 5 seconds each
- Limited external dependencies (mocked AWS)
- Test component boundaries
- Focus on data flow

### End-to-End Tests (`@pytest.mark.e2e`)

Full workflow tests that simulate real usage:

- Complete API workflows
- Full machine lifecycle
- Error handling scenarios
- Performance under load

**Characteristics:**
- Run in < 30 seconds each
- Comprehensive mocking
- Test complete user journeys
- Validate business requirements

### Performance Tests (`@pytest.mark.slow`)

Tests that measure performance and scalability:

- Load testing
- Memory usage
- Response times
- Concurrent operations

### AWS Tests (`@pytest.mark.aws`)

Tests specific to AWS provider functionality:

- AWS service interactions (mocked)
- AWS-specific error handling
- AWS configuration validation

### Security Tests (`@pytest.mark.security`)

Security-focused tests:

- Input validation
- Authentication/authorization
- Vulnerability scanning
- Compliance checks

## Running Tests

### Quick Start

```bash
# Run all tests
make test

# Run specific test categories
make test-unit
make test-integration
make test-e2e

# Run with coverage
make test-html
```

### Using Test Runner Scripts

```bash
# Python test runner
./run_tests.py --unit --coverage
./run_tests.py --integration --parallel
./run_tests.py --e2e --verbose

# Bash test runner
./test_runner.sh unit
./test_runner.sh integration
./test_runner.sh all
./test_runner.sh ci
```

### Direct pytest Commands

```bash
# Run unit tests
pytest tests/unit/ -m "unit and not slow" -v

# Run integration tests
pytest tests/integration/ -m "integration" -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html

# Run specific test file
pytest tests/unit/domain/test_template_aggregate.py -v

# Run tests matching pattern
pytest -k "test_template" -v

# Run with markers
pytest -m "unit and aws" -v
```

### Parallel Execution

```bash
# Run tests in parallel
pytest tests/ -n auto

# Run with specific number of workers
pytest tests/ -n 4
```

## Test Configuration

### pytest.ini

The `pytest.ini` file contains comprehensive test configuration:

- Test discovery patterns
- Markers for categorization
- Environment variables
- Timeout settings
- Coverage configuration
- Warning filters

### conftest.py

Global test configuration includes:

- **Fixtures**: Reusable test data and mocks
- **Environment Setup**: Test environment configuration
- **AWS Mocking**: Comprehensive AWS service mocking
- **Test Utilities**: Helper functions and builders

### Key Fixtures

```python
# Configuration fixtures
@pytest.fixture
def test_config_dict() -> Dict[str, Any]

@pytest.fixture
def config_manager(test_config_dict) -> ConfigurationManager

# AWS fixtures
@pytest.fixture
def aws_mocks()  # Comprehensive AWS mocking

@pytest.fixture
def mock_ec2_resources()  # Mock EC2 resources

# Domain fixtures
@pytest.fixture
def sample_template() -> Template

@pytest.fixture
def sample_request() -> Request

@pytest.fixture
def sample_machine() -> Machine

# Application fixtures
@pytest.fixture
def application_service() -> ApplicationService
```

## Writing Tests

### Test Naming Convention

```python
# Test classes
class TestTemplateAggregate:
    """Test cases for Template aggregate."""

# Test methods
def test_template_creation(self):
    """Test basic template creation."""

def test_template_validation_invalid_provider_api(self):
    """Test template validation with invalid provider API."""
```

### Test Structure

```python
@pytest.mark.unit
class TestTemplateAggregate:
    """Test cases for Template aggregate."""

    def test_template_creation(self):
        """Test basic template creation."""
        # Arrange
        template_data = {
            "id": "template-001",
            "name": "test-template",
            # ... other fields
        }

        # Act
        template = Template(**template_data)

        # Assert
        assert template.id == "template-001"
        assert template.name == "test-template"
```

### Using Fixtures

```python
def test_application_service_request_machines(
    self,
    application_service: ApplicationService,
    mock_command_bus: Mock,
    sample_template: Template
):
    """Test requesting machines through application service."""
    # Setup
    mock_command_bus.dispatch.return_value = {"request_id": "req-123"}

    # Execute
    result = application_service.request_machines(
        template_id=sample_template.id,
        machine_count=2,
        requester_id="test-user"
    )

    # Verify
    assert result["request_id"] == "req-123"
    mock_command_bus.dispatch.assert_called_once()
```

### Mocking AWS Services

```python
@pytest.mark.aws
def test_aws_provider_provision_instances(
    self,
    aws_config: AWSConfig,
    aws_mocks,  # Enables moto mocking
    mock_ec2_resources
):
    """Test AWS provider instance provisioning."""
    provider = AWSProvider(config=aws_config)
    provider.initialize(aws_config)

    # Use mocked AWS services
    result = provider.provision_instances(
        template_id="template-001",
        count=2,
        configuration={
            "subnet_id": mock_ec2_resources["subnet_id"],
            "security_group_ids": [mock_ec2_resources["security_group_id"]]
        }
    )

    assert "instance_ids" in result
```

### Parametrized Tests

```python
@pytest.mark.parametrize("instance_type", [
    "t2.micro", "t2.small", "t3.medium", "m5.large"
])
def test_instance_type_validation(self, instance_type: str):
    """Test instance type validation with various types."""
    instance_type_obj = InstanceType(instance_type)
    assert instance_type_obj.value == instance_type
```

## Continuous Integration

### GitHub Actions Workflows

1. **CI Pipeline** (`.github/workflows/ci.yml`):
   - Code quality checks (linting, formatting)
   - Security scanning
   - Unit, integration, and E2E tests
   - Coverage reporting
   - Build and package

2. **Test Matrix** (`.github/workflows/test-matrix.yml`):
   - Multi-OS testing (Ubuntu, Windows, macOS)
   - Multi-Python version testing (3.9-3.12)
   - Comprehensive test coverage

3. **Security Scan** (`.github/workflows/security.yml`):
   - Bandit security linting
   - Safety dependency vulnerability check
   - Semgrep security analysis
   - CodeQL analysis

### CI Commands

```bash
# Run full CI pipeline locally
make ci

# Run quick CI pipeline
make ci-quick

# Run security checks
make security
```

## Coverage Reports

### Generating Coverage Reports

```bash
# Terminal coverage report
pytest tests/ --cov=src --cov-report=term-missing

# HTML coverage report
pytest tests/ --cov=src --cov-report=html
# Open htmlcov/index.html in browser

# XML coverage report (for CI)
pytest tests/ --cov=src --cov-report=xml
```

### Coverage Targets

- **Unit Tests**: > 90% line coverage, > 85% branch coverage
- **Integration Tests**: > 80% line coverage
- **Combined**: > 85% line coverage, > 80% branch coverage

### Coverage Configuration

Coverage settings in `pytest.ini`:

```ini
[coverage:run]
branch = True
source = src
omit = tests/*, setup.py, */__init__.py

[coverage:report]
exclude_lines =
    pragma: no cover
    def __repr__
    raise NotImplementedError
```

## Performance Testing

### Running Performance Tests

```bash
# Run performance tests
pytest tests/ -m "slow" --timeout=600

# Run with benchmark plugin
pytest tests/ --benchmark-only
```

### Performance Benchmarks

```python
@pytest.mark.benchmark
def test_template_loading_performance(benchmark):
    """Benchmark template loading performance."""
    result = benchmark(load_templates, count=1000)
    assert len(result) == 1000
```

## Troubleshooting

### Common Issues

1. **Import Errors**:
   ```bash
   # Ensure PYTHONPATH is set
   export PYTHONPATH="${PWD}/src:${PYTHONPATH}"
   ```

2. **AWS Credential Errors**:
   ```bash
   # Set test credentials
   export AWS_ACCESS_KEY_ID=testing
   export AWS_SECRET_ACCESS_KEY=testing
   ```

3. **Timeout Issues**:
   ```bash
   # Increase timeout for slow tests
   pytest tests/ --timeout=600
   ```

4. **Memory Issues**:
   ```bash
   # Run tests with memory profiling
   pytest tests/ --memray
   ```

### Debug Mode

```bash
# Run with debug output
pytest tests/ -v --tb=long --capture=no

# Run single test with debugging
pytest tests/unit/domain/test_template_aggregate.py::TestTemplateAggregate::test_template_creation -v -s
```

### Test Data Cleanup

```bash
# Clean test artifacts
make clean

# Reset test environment
./test_runner.sh clean
```

## Best Practices

### Test Organization

1. **One test class per production class**
2. **Group related tests in classes**
3. **Use descriptive test names**
4. **Follow AAA pattern (Arrange, Act, Assert)**

### Test Data

1. **Use fixtures for reusable test data**
2. **Create test data builders for complex objects**
3. **Avoid hardcoded values**
4. **Use parametrized tests for multiple scenarios**

### Mocking

1. **Mock external dependencies**
2. **Use specific mocks (Mock(spec=Class))**
3. **Verify mock interactions**
4. **Reset mocks between tests**

### Performance

1. **Keep unit tests fast (< 1 second)**
2. **Use markers to categorize slow tests**
3. **Run tests in parallel when possible**
4. **Profile slow tests**

### Maintenance

1. **Keep tests up to date with code changes**
2. **Remove obsolete tests**
3. **Refactor test code like production code**
4. **Monitor test execution times**

## Resources

- [pytest Documentation](https://docs.pytest.org/)
- [pytest-cov Documentation](https://pytest-cov.readthedocs.io/)
- [moto Documentation](https://docs.getmoto.org/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)

## Contributing

When contributing tests:

1. Follow the existing test structure
2. Add appropriate markers
3. Include docstrings
4. Ensure tests pass in CI
5. Maintain or improve coverage
6. Update this documentation if needed
