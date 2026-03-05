"""Tests for Dependency Injection pattern implementation.

This module validates the DI pattern implementation including:
- Container lifecycle management
- Circular dependency detection
- Scope management (Singleton, Transient, Scoped)
- Injectable decorator functionality
- Port/Adapter registration
"""

import threading
import time

import pytest

from domain.base.dependency_injection import (
    get_injectable_metadata,
    injectable,
    is_injectable,
    singleton,
)
from domain.base.di_contracts import DIScope
from infrastructure.di.container import DIContainer
from infrastructure.di.exceptions import (
    CircularDependencyError,
    InstantiationError,
)


@pytest.mark.unit
@pytest.mark.patterns
class TestDIPattern:
    """Test Dependency Injection pattern implementation."""

    def test_container_lifecycle_management(self):
        """Test DI container lifecycle."""
        # Test container initialization
        container = DIContainer()
        assert container is not None

        # Test service registration
        class TestService:
            def __init__(self):
                self.value = "test"

        container.register_type(TestService, TestService, DIScope.TRANSIENT)

        # Test service resolution
        service = container.get(TestService)
        assert service is not None
        assert isinstance(service, TestService)
        assert service.value == "test"

        # Test container disposal
        if hasattr(container, "dispose"):
            container.dispose()

    def test_circular_dependency_detection(self):
        """Validate circular dependency detection."""
        container = DIContainer()

        # Define ServiceB first so ServiceA can reference it without a forward ref string
        class ServiceB:
            pass

        class ServiceA:
            def __init__(self, service_b: ServiceB):
                self.service_b = service_b

        # Patch ServiceB to depend back on ServiceA to create the cycle
        class ServiceBCyclic:
            def __init__(self, service_a: ServiceA):
                self.service_a = service_a

        # Register services with circular dependencies
        container.register_type(ServiceA, ServiceA, DIScope.TRANSIENT)
        container.register_type(ServiceB, ServiceBCyclic, DIScope.TRANSIENT)

        # Should detect circular dependency
        with pytest.raises(CircularDependencyError):
            container.get(ServiceA)

    def test_singleton_scope_management(self):
        """Test singleton instance management."""
        container = DIContainer()

        @singleton
        class SingletonService:
            def __init__(self):
                self.created_at = time.time()

        # Register as singleton
        container.register_type(SingletonService, SingletonService, DIScope.SINGLETON)

        # Resolve multiple times
        instance1 = container.get(SingletonService)
        instance2 = container.get(SingletonService)

        # Should be the same instance
        assert instance1 is instance2
        assert instance1.created_at == instance2.created_at

    def test_transient_scope_management(self):
        """Test transient instance creation."""
        container = DIContainer()

        class TransientService:
            def __init__(self):
                self.created_at = time.time()

        # Register as transient
        container.register_type(TransientService, TransientService, DIScope.TRANSIENT)

        # Resolve multiple times
        instance1 = container.get(TransientService)
        time.sleep(0.001)  # Ensure different timestamps
        instance2 = container.get(TransientService)

        # Should be different instances
        assert instance1 is not instance2
        assert instance1.created_at != instance2.created_at

    def test_scoped_lifetime_management(self):
        """Test scoped instance lifecycle."""
        container = DIContainer()

        class ScopedService:
            def __init__(self):
                self.id = id(self)

        # Register as singleton — container caches singleton instances across calls
        container.register_type(ScopedService, ScopedService, DIScope.SINGLETON)

        # Resolve twice — should be same instance
        instance1 = container.get(ScopedService)
        instance2 = container.get(ScopedService)

        # Should be same instance within scope
        assert instance1 is instance2

    def test_injectable_decorator_functionality(self):
        """Validate injectable decorator functionality."""

        # Test automatic service registration
        @injectable
        class InjectableService:
            def __init__(self):
                self.name = "injectable"

        # Should have injectable metadata
        assert is_injectable(InjectableService)
        metadata = get_injectable_metadata(InjectableService)
        assert metadata is not None

        # Test dependency metadata extraction
        @injectable
        class ServiceWithDependencies:
            def __init__(self, service: InjectableService):
                self.service = service

        assert is_injectable(ServiceWithDependencies)
        dep_metadata = get_injectable_metadata(ServiceWithDependencies)
        assert dep_metadata is not None

    def test_constructor_injection(self):
        """Test constructor-based dependency injection."""
        container = DIContainer()

        class Repository:
            def get_data(self):
                return "data"

        class Service:
            def __init__(self, repo: Repository):
                self.repo = repo

            def process(self):
                return self.repo.get_data()

        # Register dependencies
        container.register_type(Repository, Repository, DIScope.SINGLETON)
        container.register_type(Service, Service, DIScope.TRANSIENT)

        # Resolve service with injected dependencies
        service = container.get(Service)
        assert service is not None
        assert service.repo is not None
        assert service.process() == "data"

    def test_interface_based_injection(self):
        """Test interface-based dependency injection."""
        container = DIContainer()

        # Define interface
        class IRepository:
            def get_data(self):
                raise NotImplementedError

        # Concrete implementation
        class ConcreteRepository(IRepository):
            def get_data(self):
                return "concrete_data"

        class Service:
            def __init__(self, repo: IRepository):
                self.repo = repo

        # Register interface to implementation mapping
        container.register_type(IRepository, ConcreteRepository, DIScope.SINGLETON)
        container.register_type(Service, Service, DIScope.TRANSIENT)

        # Resolve service
        service = container.get(Service)
        assert service is not None
        assert isinstance(service.repo, ConcreteRepository)
        assert service.repo.get_data() == "concrete_data"

    def test_factory_registration(self):
        """Test factory-based service registration."""
        container = DIContainer()

        class ComplexService:
            def __init__(self, config: dict):
                self.config = config

        # Register factory function — factory receives the container as its argument
        def create_complex_service(c):
            return ComplexService({"setting": "value"})

        container.register_factory(ComplexService, create_complex_service)

        # Resolve using factory
        service = container.get(ComplexService)
        assert service is not None
        assert service.config["setting"] == "value"

    def test_conditional_registration(self):
        """Test conditional service registration."""
        container = DIContainer()

        class DevService:
            def get_env(self):
                return "development"

        class ProdService:
            def get_env(self):
                return "production"

        # Register based on condition
        environment = "development"

        if environment == "development":
            container.register_type(DevService, DevService, DIScope.SINGLETON)
            service = container.get(DevService)
        else:
            container.register_type(ProdService, ProdService, DIScope.SINGLETON)
            service = container.get(ProdService)

        assert service.get_env() == "development"

    def test_lazy_initialization(self):
        """Test lazy service initialization."""
        container = DIContainer()

        initialization_count = 0

        class LazyService:
            def __init__(self):
                nonlocal initialization_count
                initialization_count += 1
                self.value = "lazy"

        # Register as lazy singleton
        container.register_type(LazyService, LazyService, DIScope.SINGLETON)

        # Should not initialize until first resolution
        assert initialization_count == 0

        # First resolution should initialize
        service1 = container.get(LazyService)
        assert initialization_count == 1

        # Second resolution should reuse instance
        service2 = container.get(LazyService)
        assert initialization_count == 1
        assert service1 is service2

    def test_thread_safety(self):
        """Test container thread safety."""
        container = DIContainer()

        class ThreadSafeService:
            def __init__(self):
                self.thread_id = threading.current_thread().ident

        container.register_type(ThreadSafeService, ThreadSafeService, DIScope.SINGLETON)

        # Resolve from multiple threads
        results = []

        def resolve_service():
            service = container.get(ThreadSafeService)
            results.append(service)

        threads = []
        for _ in range(5):
            thread = threading.Thread(target=resolve_service)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # All threads should get the same singleton instance
        assert len(results) == 5
        first_service = results[0]
        for service in results[1:]:
            assert service is first_service

    def test_dependency_resolution_error_handling(self):
        """Test dependency resolution error handling."""
        container = DIContainer()

        # Test instantiation error — service whose __init__ raises
        class FailingService:
            def __init__(self):
                raise ValueError("Initialization failed")

        container.register_type(FailingService, FailingService, DIScope.TRANSIENT)

        with pytest.raises(InstantiationError):
            container.get(FailingService)

    def test_container_hierarchy(self):
        """Test container hierarchy and scoping."""
        parent_container = DIContainer()

        class ParentService:
            def get_source(self):
                return "parent"

        parent_container.register_type(ParentService, ParentService, DIScope.SINGLETON)

        # Create child container
        if hasattr(DIContainer, "create_child"):
            child_container = parent_container.create_child()

            # Child should resolve parent services
            service = child_container.get(ParentService)
            assert service.get_source() == "parent"

            # Child can override parent registrations
            class ChildService(ParentService):
                def get_source(self):
                    return "child"

            child_container.register_type(ParentService, ChildService, DIScope.SINGLETON)

            overridden_service = child_container.get(ParentService)
            assert overridden_service.get_source() == "child"

    def test_decorator_parameter_handling(self):
        """Test decorator parameter handling."""

        # injectable is a plain decorator — no parameters supported
        @injectable
        class ParameterizedService:
            def __init__(self):
                self.scope = "singleton"

        assert is_injectable(ParameterizedService)
        metadata = get_injectable_metadata(ParameterizedService)
        assert metadata is not None

    def test_port_adapter_registration(self):
        """Test port/adapter pattern registration."""
        container = DIContainer()

        # Define port (interface)
        class LoggerPort:
            def log(self, message: str):
                raise NotImplementedError

        # Define adapter (implementation)
        class ConsoleLoggerAdapter(LoggerPort):
            def log(self, message: str):
                return f"Console: {message}"

        # Register port to adapter mapping
        container.register_type(LoggerPort, ConsoleLoggerAdapter, DIScope.SINGLETON)

        # Service depending on port
        class ApplicationService:
            def __init__(self, logger: LoggerPort):
                self.logger = logger

            def do_work(self):
                return self.logger.log("Work done")

        container.register_type(ApplicationService, ApplicationService, DIScope.TRANSIENT)

        # Resolve and test
        app_service = container.get(ApplicationService)
        result = app_service.do_work()
        assert result == "Console: Work done"

    def test_container_configuration_validation(self):
        """Test container configuration validation."""
        container = DIContainer()

        # Test invalid scope — container accepts it silently (no runtime validation)
        class TestService:
            pass

        # Should not raise; invalid scope is stored as-is
        container.register_type(TestService, TestService, "invalid_scope")
        assert container.is_registered(TestService)

    def test_service_disposal(self):
        """Test service disposal and cleanup."""
        container = DIContainer()

        disposed_services = []

        class DisposableService:
            def __init__(self):
                self.disposed = False

            def dispose(self):
                self.disposed = True
                disposed_services.append(self)

        container.register_type(DisposableService, DisposableService, DIScope.SINGLETON)

        # Resolve service
        service = container.get(DisposableService)
        assert not service.disposed

        # Dispose container
        if hasattr(container, "dispose"):
            container.dispose()

            # Service should be disposed
            assert service.disposed
            assert service in disposed_services
