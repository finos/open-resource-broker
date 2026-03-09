"""Verify resilience strategies have no botocore dependency."""
import ast
import pathlib


def _get_imports(filepath: pathlib.Path) -> set[str]:
    """Extract all import module names from a Python file."""
    tree = ast.parse(filepath.read_text())
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split('.')[0])
    return imports


def test_circuit_breaker_no_botocore():
    """circuit_breaker.py must not import botocore."""
    path = pathlib.Path("src/orb/infrastructure/resilience/strategy/circuit_breaker.py")
    imports = _get_imports(path)
    assert "botocore" not in imports, "circuit_breaker.py still imports botocore"


def test_exponential_no_botocore():
    """exponential.py must not import botocore."""
    path = pathlib.Path("src/orb/infrastructure/resilience/strategy/exponential.py")
    imports = _get_imports(path)
    assert "botocore" not in imports, "exponential.py still imports botocore"


def test_circuit_breaker_no_non_retryable_codes():
    """circuit_breaker.py must not have NON_RETRYABLE_CODES."""
    path = pathlib.Path("src/orb/infrastructure/resilience/strategy/circuit_breaker.py")
    source = path.read_text()
    assert "NON_RETRYABLE_CODES" not in source, "circuit_breaker.py still has NON_RETRYABLE_CODES"


def test_exponential_no_non_retryable_codes():
    """exponential.py must not have NON_RETRYABLE_CODES."""
    path = pathlib.Path("src/orb/infrastructure/resilience/strategy/exponential.py")
    source = path.read_text()
    assert "NON_RETRYABLE_CODES" not in source, "exponential.py still has NON_RETRYABLE_CODES"


# --- New tests for task: remove AWS service names from generic resilience layer ---

AWS_SERVICE_NAMES = ("ec2", "dynamodb", "s3")

RESILIENCE_FILES = [
    pathlib.Path("src/orb/infrastructure/resilience/retry_decorator.py"),
    pathlib.Path("src/orb/infrastructure/resilience/__init__.py"),
    pathlib.Path("src/orb/infrastructure/resilience/strategy/exponential.py"),
    pathlib.Path("src/orb/infrastructure/resilience/config.py"),
]

def _get_string_literals(filepath: pathlib.Path) -> set[str]:
    """Extract all string literal values from a Python file via AST."""
    tree = ast.parse(filepath.read_text())
    literals = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
    return literals


def test_resilience_files_no_aws_service_string_literals():
    """No AWS service name string literals in the generic resilience layer."""
    for path in RESILIENCE_FILES:
        literals = _get_string_literals(path)
        for svc in AWS_SERVICE_NAMES:
            assert svc not in literals, (
                f"{path} still contains AWS service name literal '{svc}'"
            )


def test_performance_schema_no_aws_service_string_literals():
    """No AWS service name string literals in performance_schema CircuitBreakerConfig."""
    path = pathlib.Path("src/orb/config/schemas/performance_schema.py")
    literals = _get_string_literals(path)
    for svc in AWS_SERVICE_NAMES:
        assert svc not in literals, (
            f"performance_schema.py still contains AWS service name literal '{svc}'"
        )


def test_exponential_no_service_parameter():
    """ExponentialBackoffStrategy.__init__ must not have a 'service' parameter."""
    path = pathlib.Path("src/orb/infrastructure/resilience/strategy/exponential.py")
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            param_names = [arg.arg for arg in node.args.args]
            assert "service" not in param_names, (
                "ExponentialBackoffStrategy.__init__ still has 'service' parameter"
            )


def test_retry_decorator_no_get_retry_config_for_service():
    """get_retry_config_for_service must not exist in retry_decorator.py."""
    path = pathlib.Path("src/orb/infrastructure/resilience/retry_decorator.py")
    source = path.read_text()
    assert "get_retry_config_for_service" not in source, (
        "retry_decorator.py still defines get_retry_config_for_service"
    )


def test_init_no_get_retry_config_for_service():
    """get_retry_config_for_service must not be exported from resilience __init__.py."""
    path = pathlib.Path("src/orb/infrastructure/resilience/__init__.py")
    source = path.read_text()
    assert "get_retry_config_for_service" not in source, (
        "resilience __init__.py still exports get_retry_config_for_service"
    )


def test_resilience_config_no_get_service_config():
    """RetryConfig.get_service_config must not exist in config.py."""
    path = pathlib.Path("src/orb/infrastructure/resilience/config.py")
    source = path.read_text()
    assert "get_service_config" not in source, (
        "resilience/config.py still defines get_service_config"
    )


def test_storage_schema_no_service_configs_field():
    """RetryConfig in storage_schema.py must not have a service_configs field."""
    path = pathlib.Path("src/orb/config/schemas/storage_schema.py")
    source = path.read_text()
    assert "service_configs" not in source, (
        "storage_schema.py still defines service_configs field"
    )


def test_performance_schema_no_service_configs_field():
    """CircuitBreakerConfig in performance_schema.py must not have service_configs."""
    path = pathlib.Path("src/orb/config/schemas/performance_schema.py")
    source = path.read_text()
    assert "service_configs" not in source, (
        "performance_schema.py still defines service_configs field"
    )


def test_performance_schema_no_retryable_exceptions_field():
    """CircuitBreakerConfig in performance_schema.py must not have retryable_exceptions."""
    path = pathlib.Path("src/orb/config/schemas/performance_schema.py")
    source = path.read_text()
    assert "retryable_exceptions" not in source, (
        "performance_schema.py still defines retryable_exceptions field"
    )
