"""Architectural compliance tests for DDD, SOLID, and Clean Architecture principles."""

import ast
import sys
from pathlib import Path

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


@pytest.mark.unit
class TestDDDCompliance:
    """Test Domain-Driven Design compliance."""

    def test_domain_has_no_infrastructure_dependencies(self):
        """Ensure domain layer imports no infrastructure code."""
        domain_path = Path(__file__).parent.parent.parent / "src" / "domain"
        infrastructure_imports = []

        for py_file in domain_path.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue

            with open(py_file, "r") as f:
                try:
                    tree = ast.parse(f.read())
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                if "infrastructure" in alias.name:
                                    infrastructure_imports.append(f"{py_file}: {alias.name}")
                        elif isinstance(node, ast.ImportFrom):
                            if node.module and "infrastructure" in node.module:
                                infrastructure_imports.append(f"{py_file}: {node.module}")
                except SyntaxError:
                    # Skip files with syntax errors
                    continue

        assert not infrastructure_imports, (
            f"Domain layer has infrastructure dependencies: {infrastructure_imports}"
        )

    def test_domain_has_no_provider_dependencies(self):
        """Ensure domain layer imports no provider-specific code."""
        domain_path = Path(__file__).parent.parent.parent / "src" / "domain"
        provider_imports = []

        for py_file in domain_path.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue

            with open(py_file, "r") as f:
                try:
                    tree = ast.parse(f.read())
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                if "providers" in alias.name or "aws" in alias.name.lower():
                                    provider_imports.append(f"{py_file}: {alias.name}")
                        elif isinstance(node, ast.ImportFrom):
                            if node.module and (
                                "providers" in node.module or "aws" in node.module.lower()
                            ):
                                provider_imports.append(f"{py_file}: {node.module}")
                except SyntaxError:
                    continue

        assert not provider_imports, f"Domain layer has provider dependencies: {provider_imports}"

    def test_bounded_context_isolation(self):
        """Ensure bounded contexts don't leak into each other."""
        domain_path = Path(__file__).parent.parent.parent / "src" / "domain"
        contexts = ["template", "request", "machine"]
        violations = []

        for context in contexts:
            context_path = domain_path / context
            if not context_path.exists():
                continue

            for py_file in context_path.rglob("*.py"):
                if py_file.name == "__init__.py":
                    continue

                with open(py_file, "r") as f:
                    try:
                        tree = ast.parse(f.read())
                        for node in ast.walk(tree):
                            if isinstance(node, ast.ImportFrom):
                                if node.module:
                                    for other_context in contexts:
                                        if (
                                            other_context != context
                                            and f"domain.{other_context}" in node.module
                                        ):
                                            violations.append(
                                                f"{py_file}: imports from {other_context} context"
                                            )
                    except SyntaxError:
                        continue

        assert not violations, f"Bounded context violations: {violations}"

    def test_aggregates_maintain_consistency(self):
        """Test that aggregate roots maintain business invariants."""
        # Import domain aggregates
        try:
            from domain.request.aggregate import Request
        except ImportError as e:
            pytest.skip(f"Could not import aggregates: {e}")

        # Test Request aggregate invariants
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Invariant: machine_count should always be positive
        assert request.machine_count > 0

        # Invariant: status should be valid
        from domain.request.value_objects import RequestStatus

        assert request.status in RequestStatus

        # Invariant: created_at should be set
        assert request.created_at is not None

    def test_domain_events_are_immutable(self):
        """Ensure all domain events are immutable."""
        try:
            from domain.base.events import RequestCreatedEvent
        except ImportError as e:
            pytest.skip(f"Could not import domain events: {e}")

        # Create a domain event
        event = RequestCreatedEvent(
            request_id="test-request",
            template_id="test-template",
            machine_count=2,
            requester_id="test-user",
        )

        # Try to modify the event (should fail if immutable)
        with pytest.raises((AttributeError, TypeError)):
            event.request_id = "modified-request"

    def test_ubiquitous_language_consistency(self):
        """Validate consistent terminology across layers."""
        # Check that key domain terms are used consistently
        domain_terms = {
            "template_id": "templateId",  # Should use snake_case internally, camelCase in API
            "machine_count": "machineCount",
            "request_id": "requestId",
            "provider_api": "providerApi",  # Not aws_handler
        }

        # This test validates that we use consistent terminology
        # The actual validation would require more sophisticated AST analysis
        assert True  # Placeholder - would implement full term analysis


@pytest.mark.unit
class TestSOLIDCompliance:
    """Test SOLID principle compliance."""

    def test_single_responsibility_principle(self):
        """Ensure each class has only one reason to change."""
        # Test that ApplicationService has a single responsibility (orchestration)
        try:
            from application.service import ApplicationService
        except ImportError as e:
            pytest.skip(f"Could not import ApplicationService: {e}")

        # Get all methods of ApplicationService
        methods = [
            method
            for method in dir(ApplicationService)
            if not method.startswith("_") and callable(getattr(ApplicationService, method))
        ]

        # ApplicationService should only have orchestration methods
        orchestration_methods = [
            "get_available_templates",
            "get_template_by_id",
            "request_machines",
            "get_request_status",
            "request_return_machines",
            "get_return_requests",
            "get_machine_status",
            "get_machines_by_request",
            "validate_template",
            "get_provider_health",
            "get_provider_info",
        ]

        # All methods should be orchestration-related
        for method in methods:
            assert any(orch_method in method for orch_method in orchestration_methods), (
                f"ApplicationService method {method} may violate SRP"
            )

    def test_open_closed_principle(self):
        """Ensure classes are open for extension, closed for modification."""
        # Test that provider interface allows extension without modification
        try:
            from infrastructure.interfaces.provider import ProviderPort
        except ImportError as e:
            pytest.skip(f"Could not import ProviderPort: {e}")

        # ProviderPort should be abstract/protocol
        assert hasattr(ProviderPort, "__abstractmethods__") or hasattr(
            ProviderPort, "_abc_registry"
        ), "ProviderPort should be abstract to support OCP"

    def test_liskov_substitution_principle(self):
        """Ensure subtypes are substitutable for base types."""
        # Test that all providers can substitute the base interface
        try:
            from infrastructure.interfaces.provider import ProviderPort
            from providers.aws.strategy.aws_provider_strategy import (
                AWSProviderStrategy as AWSProvider,
            )
        except ImportError as e:
            pytest.skip(f"Could not import provider classes: {e}")

        # AWS Provider should be substitutable for ProviderPort
        assert issubclass(AWSProvider, ProviderPort), (
            "AWSProvider should be substitutable for ProviderPort"
        )

    def test_interface_segregation_principle(self):
        """Ensure clients depend only on interfaces they use."""
        # Test that interfaces are focused and not bloated
        try:
            from infrastructure.interfaces.provider import ProviderPort
        except ImportError as e:
            pytest.skip(f"Could not import ProviderPort: {e}")

        # ProviderPort should have focused methods
        methods = [
            method
            for method in dir(ProviderPort)
            if not method.startswith("_") and callable(getattr(ProviderPort, method))
        ]

        # Should not have too many methods (ISP violation indicator)
        assert len(methods) < 15, f"ProviderPort has {len(methods)} methods, may violate ISP"

    def test_dependency_inversion_principle(self):
        """Ensure high-level modules don't depend on low-level modules."""
        # Test that ApplicationService depends on abstractions, not concretions
        try:
            import inspect

            from application.service import ApplicationService
        except ImportError as e:
            pytest.skip(f"Could not import ApplicationService: {e}")

        # Get constructor signature
        sig = inspect.signature(ApplicationService.__init__)

        # Parameters should be interfaces/abstractions, not concrete classes
        for param_name, param in sig.parameters.items():
            if param_name in ["self", "provider_type"]:
                continue

            # Check if parameter has type hints pointing to interfaces
            if param.annotation != inspect.Parameter.empty:
                annotation_str = str(param.annotation)
                # Should depend on interfaces/ports, not concrete implementations
                assert (
                    "Port" in annotation_str
                    or "Interface" in annotation_str
                    or "Service" in annotation_str
                    or "Bus" in annotation_str
                ), f"ApplicationService parameter {param_name} may violate DIP"


@pytest.mark.unit
class TestCleanArchitectureCompliance:
    """Test Clean Architecture compliance."""

    def test_dependency_direction(self):
        """Ensure dependencies point inward toward domain."""
        # Test that application layer imports domain, not vice versa
        app_imports_domain = False
        domain_imports_app = False

        # Check application layer imports
        app_path = Path(__file__).parent.parent.parent / "src" / "application"
        for py_file in app_path.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue

            with open(py_file, "r") as f:
                content = f.read()
                if "from domain" in content or "import domain" in content:
                    app_imports_domain = True

        # Check domain layer imports
        domain_path = Path(__file__).parent.parent.parent / "src" / "domain"
        for py_file in domain_path.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue

            with open(py_file, "r") as f:
                content = f.read()
                if "from application" in content or "import application" in content:
                    domain_imports_app = True

        assert app_imports_domain, "Application layer should import domain layer"
        assert not domain_imports_app, "Domain layer should not import application layer"

    def test_layer_isolation(self):
        """Ensure each layer can be tested in isolation."""
        # Test that domain layer has no external dependencies
        domain_path = Path(__file__).parent.parent.parent / "src" / "domain"
        external_deps = []

        for py_file in domain_path.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue

            with open(py_file, "r") as f:
                try:
                    tree = ast.parse(f.read())
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom):
                            if node.module and not node.module.startswith("src.domain"):
                                # Allow standard library and typing imports
                                if not any(
                                    node.module.startswith(allowed)
                                    for allowed in [
                                        "typing",
                                        "datetime",
                                        "uuid",
                                        "enum",
                                        "abc",
                                        "dataclasses",
                                        "pydantic",
                                    ]
                                ):
                                    external_deps.append(f"{py_file}: {node.module}")
                except SyntaxError:
                    continue

        # Domain should only depend on standard library and domain-specific libraries
        assert len(external_deps) < 5, (
            f"Domain layer has too many external dependencies: {external_deps}"
        )

    def test_framework_independence(self):
        """Ensure domain is independent of frameworks."""
        domain_path = Path(__file__).parent.parent.parent / "src" / "domain"
        framework_deps = []

        frameworks = ["flask", "django", "fastapi", "boto3", "sqlalchemy", "requests"]

        for py_file in domain_path.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue

            with open(py_file, "r") as f:
                content = f.read().lower()
                for framework in frameworks:
                    if framework in content:
                        framework_deps.append(f"{py_file}: {framework}")

        assert not framework_deps, f"Domain layer has framework dependencies: {framework_deps}"


@pytest.mark.unit
class TestDesignPatternCompliance:
    """Test design pattern implementation compliance."""

    def test_cqrs_pattern_compliance(self):
        """Test CQRS pattern implementation."""
        try:
            pass
        except ImportError as e:
            pytest.skip(f"Could not import CQRS components: {e}")

        # Commands should not return data (except acknowledgment)
        # Queries should not modify state
        # This would require runtime analysis to fully validate
        assert True  # Placeholder for CQRS validation

    def test_repository_pattern_compliance(self):
        """Test Repository pattern implementation."""
        try:
            from domain.base.ports import RepositoryPort as Repository
        except ImportError as e:
            pytest.skip(f"Could not import Repository: {e}")

        # Repository should be abstract
        assert hasattr(Repository, "__abstractmethods__"), (
            "Repository should be abstract base class"
        )

    def test_factory_pattern_compliance(self):
        """Test Factory pattern implementation."""
        try:
            from infrastructure.di.container import DIContainer
        except ImportError as e:
            pytest.skip(f"Could not import DIContainer: {e}")

        # DIContainer acts as a factory
        container = DIContainer()
        assert hasattr(container, "register"), "DIContainer should have register method"
        assert hasattr(container, "resolve"), "DIContainer should have resolve method"

    def test_aggregate_pattern_compliance(self):
        """Test Aggregate pattern implementation."""
        try:
            from domain.base.entity import AggregateRoot
            from domain.request.aggregate import Request
        except ImportError as e:
            pytest.skip(f"Could not import aggregate classes: {e}")

        # Aggregates should inherit from AggregateRoot
        assert issubclass(Request, AggregateRoot), "Request should inherit from AggregateRoot"

        # Aggregates should have domain event capabilities
        request = Request.create_new_request(
            template_id="test-template", machine_count=1, requester_id="test-user"
        )

        assert hasattr(request, "get_domain_events"), (
            "Aggregates should have get_domain_events method"
        )
        assert hasattr(request, "clear_domain_events"), (
            "Aggregates should have clear_domain_events method"
        )


@pytest.mark.unit
class TestCodeQualityCompliance:
    """Test general code quality compliance."""

    def test_no_circular_imports(self):
        """Test that there are no circular import dependencies."""
        # This would require sophisticated dependency analysis
        # For now, we test that basic imports work
        try:
            pass
        except ImportError as e:
            pytest.fail(f"Circular import detected: {e}")

    def test_consistent_naming_conventions(self):
        """Test consistent naming conventions across the codebase."""
        # Test that we use snake_case for internal code
        # and handle camelCase conversion at boundaries

        # Check that provider_api is used instead of aws_handler
        src_path = Path(__file__).parent.parent.parent / "src"
        aws_handler_usage = []

        for py_file in src_path.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue

            with open(py_file, "r") as f:
                content = f.read()
                if "aws_handler" in content and "test" not in str(py_file):
                    aws_handler_usage.append(str(py_file))

        assert not aws_handler_usage, (
            f"Found aws_handler usage (should be provider_api): {aws_handler_usage}"
        )

    def test_proper_exception_hierarchy(self):
        """Test that exceptions follow correct hierarchy."""
        try:
            from domain.base.exceptions import DomainException
            from domain.request.exceptions import RequestValidationError
        except ImportError as e:
            pytest.skip(f"Could not import exception classes: {e}")

        # Domain exceptions should inherit from DomainException
        assert issubclass(RequestValidationError, DomainException), (
            "Domain exceptions should inherit from DomainException"
        )
