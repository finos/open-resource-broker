# Testing Guide

This guide covers testing strategies, frameworks, and best practices for the Open Host Factory Plugin.

## Testing Strategy

The plugin uses a comprehensive testing approach with multiple test levels:

### Unit Tests
- **Location**: `tests/unit/`
- **Framework**: pytest
- **Coverage**: Individual components and functions
- **Mocking**: Extensive use of mocks for external dependencies

### Integration Tests
- **Location**: `tests/integration/`
- **Framework**: pytest
- **Coverage**: Component interactions and workflows
- **Environment**: Test containers and mock services

### Performance Tests
- **Location**: `tests/performance/`
- **Framework**: pytest + psutil
- **Coverage**: Startup time, memory usage, component loading performance
- **Benchmarks**: Automated performance regression testing

### End-to-End Tests
- **Location**: `tests/e2e/`
- **Framework**: pytest + requests
- **Coverage**: Full API workflows
- **Environment**: Docker Compose test environment

## Running Tests

### All Tests
```bash
pytest
```

### Unit Tests Only
```bash
pytest tests/unit/
```

### Integration Tests
```bash
pytest tests/integration/
```

### Performance Tests
```bash
# Run performance benchmarks
pytest tests/performance/ -v

# Run specific performance tests
PYTHONPATH=. python tests/performance/test_lazy_loading_performance.py

# Run lazy loading integration tests
PYTHONPATH=. python tests/integration/test_lazy_loading_integration.py
```

### With Coverage
```bash
pytest --cov=src --cov-report=html
```

## Test Configuration

Tests use the configuration in `pytest.ini` and `tests/conftest.py`.

## Related Documentation
- [Development Guide](../development/testing.md) - Detailed testing implementation
- [Developer Guide](../developer_guide/architecture.md) - Architecture for testing
