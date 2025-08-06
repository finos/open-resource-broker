# Testing Guide

## Overview

Comprehensive testing guide for the Open Host Factory Plugin, including unit tests, integration tests, and Docker containerization testing.

## Test Categories

### Unit Tests

Fast, isolated tests for individual components:

```bash
# Run unit tests
pytest -m unit

# Run with coverage
pytest -m unit --cov=src --cov-report=html
```

### Integration Tests

Test component interactions:

```bash
# Run integration tests
pytest -m integration

# Run API integration tests
pytest -m api
```

### Docker Tests

Test Docker containerization:

```bash
# Run Docker tests
pytest -m docker

# Run Docker test script
./scripts/test-docker.sh
```

### End-to-End Tests

Full workflow testing:

```bash
# Run E2E tests
pytest -m e2e

# Run slow tests
pytest -m slow
```

## Test Structure

```
tests/
+--- unit/                   # Unit tests
|   +--- domain/            # Domain layer tests
|   +--- application/       # Application layer tests
|   +--- infrastructure/    # Infrastructure tests
+--- integration/           # Integration tests
|   +--- api/              # API integration tests
|   +--- aws/              # AWS integration tests
+--- docker/               # Docker tests
|   +--- test_dockerfile.py
|   +--- test_container_integration.py
|   +--- test_docker_compose.py
+--- e2e/                  # End-to-end tests
+--- performance/          # Performance tests
+--- security/             # Security tests
```

## Running Tests

### All Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html --cov-report=term-missing
```

### Specific Test Categories

```bash
# Unit tests only
pytest -m unit

# Integration tests
pytest -m integration

# Docker tests
pytest -m docker

# AWS tests (requires AWS credentials)
pytest -m aws

# Security tests
pytest -m security

# Performance tests
pytest -m performance
```

### Test Configuration

The project uses `pytest.ini` for configuration:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

markers =
    unit: Unit tests - fast, isolated tests
    integration: Integration tests - test component interactions
    docker: Docker containerization tests
    aws: Tests that interact with AWS services (mocked)
    security: Security-related tests
    performance: Performance and load tests
```

## Docker Testing

### Dockerfile Testing

Tests Docker build process and container structure:

```python
def test_dockerfile_structure(self, dockerfile_path):
    """Test Dockerfile structure and best practices."""
    content = dockerfile_path.read_text()
    
    # Check for multi-stage build
    assert "FROM python:3.11-slim as builder" in content
    assert "FROM python:3.11-slim as production" in content
    
    # Check for security best practices
    assert "RUN groupadd -r ohfp && useradd -r -g ohfp" in content
    assert "USER ohfp" in content
```

### Container Integration Testing

Tests container functionality and configuration:

```python
def test_container_environment_variables(self, built_image):
    """Test container responds to environment variables."""
    result = subprocess.run([
        "docker", "run", "--rm",
        "-e", "HF_SERVER_ENABLED=true",
        "-e", "HF_AUTH_ENABLED=false",
        built_image, "version"
    ], capture_output=True, text=True, timeout=30)
    
    assert result.returncode == 0
```

### Docker Compose Testing

Tests Docker Compose configurations:

```python
def test_docker_compose_dev_service_configuration(self, project_root):
    """Test development Docker Compose service configuration."""
    with open(project_root / "docker-compose.yml") as f:
        compose_config = yaml.safe_load(f)
    
    assert "ohfp-api" in compose_config["services"]
    assert "build" in compose_config["services"]["ohfp-api"]
```

## Test Utilities

### Fixtures

Common test fixtures in `tests/conftest.py`:

```python
@pytest.fixture
def mock_aws_credentials():
    """Mock AWS credentials for testing."""
    with mock.patch.dict(os.environ, {
        'AWS_ACCESS_KEY_ID': 'testing',
        'AWS_SECRET_ACCESS_KEY': 'testing',
        'AWS_SECURITY_TOKEN': 'testing',
        'AWS_SESSION_TOKEN': 'testing',
    }):
        yield

@pytest.fixture
def mock_ec2():
    """Mock EC2 service for testing."""
    with mock_aws():
        yield boto3.client('ec2', region_name='us-east-1')
```

### Test Data Builders

Factory pattern for test data:

```python
class TemplateBuilder:
    def __init__(self):
        self.template_data = {
            "templateId": "test-template",
            "templateName": "Test Template",
            "provider_api": "aws",
            "imageId": "ami-12345678",
            "instanceType": "t3.micro"
        }
    
    def with_id(self, template_id: str):
        self.template_data["templateId"] = template_id
        return self
    
    def build(self) -> Template:
        return Template(**self.template_data)
```

## Continuous Integration

### GitHub Actions

```yaml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: [3.11, 3.12, 3.13]
    
    steps:
    - uses: actions/checkout@v4
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: ${{ matrix.python-version }}
    
    - name: Install dependencies
      run: |
        pip install -r requirements.txt
        pip install -r requirements-dev.txt
    
    - name: Run tests
      run: pytest --cov=src --cov-report=xml
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
```

### Docker Testing in CI

```yaml
  docker-tests:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    - name: Set up Docker Buildx
      uses: docker/setup-buildx-action@v3
    
    - name: Run Docker tests
      run: |
        pip install pytest
        pytest -m docker -v
```

## Test Best Practices

### Writing Good Tests

1. **Arrange, Act, Assert**: Structure tests clearly
2. **Descriptive Names**: Test names should describe what they test
3. **Single Responsibility**: One assertion per test when possible
4. **Independent Tests**: Tests should not depend on each other
5. **Mock External Dependencies**: Use mocks for AWS, databases, etc.

### Example Test

```python
def test_request_machines_creates_valid_request():
    # Arrange
    template = TemplateBuilder().with_id("test-template").build()
    machine_count = 2
    
    # Act
    request = create_machine_request(template, machine_count)
    
    # Assert
    assert request.template_id == "test-template"
    assert request.machine_count == 2
    assert request.status == RequestStatus.PENDING
```

## Performance Testing

### Load Testing

```python
@pytest.mark.performance
def test_api_performance():
    """Test API performance under load."""
    import time
    import concurrent.futures
    
    def make_request():
        response = client.get("/health")
        return response.status_code == 200
    
    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(make_request) for _ in range(100)]
        results = [future.result() for future in futures]
    
    total_time = time.time() - start_time
    assert all(results)
    assert total_time < 10  # 100 requests in under 10 seconds
```

## Security Testing

### Authentication Testing

```python
@pytest.mark.security
def test_authentication_required():
    """Test that protected endpoints require authentication."""
    response = client.get("/api/v1/templates")
    assert response.status_code == 401

def test_invalid_token_rejected():
    """Test that invalid tokens are rejected."""
    headers = {"Authorization": "Bearer invalid-token"}
    response = client.get("/api/v1/templates", headers=headers)
    assert response.status_code == 401
```

For more testing examples, see the test files in the `tests/` directory.
