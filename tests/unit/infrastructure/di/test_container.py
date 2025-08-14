"""Unit tests for DIContainer integration."""

import threading
from unittest.mock import patch

import pytest

from src.domain.base.di_contracts import DIScope
from src.infrastructure.di.container import DIContainer, timed_operation
from src.infrastructure.di.exceptions import (
    CircularDependencyError,
    DependencyResolutionError,
)


class TestDIContainer:
    """Test cases for DIContainer integration."""

    def setup_method(self):
        """Set up test fixtures."""
        self.container = DIContainer()

    def test_initialization(self):
        """Test container initialization."""
        assert self.container is not None
        stats = self.container.get_stats()
        assert "service_registry" in stats
        assert "cqrs_registry" in stats
        assert "container_type" in stats
        assert stats["container_type"] == "modular"

    def test_basic_registration_and_resolution(self):
        """Test basic registration and resolution flow."""

        class TestService:
            def __init__(self):
                self.name = "test_service"

        # Register and resolve
        self.container.register_singleton(TestService)
        instance = self.container.get(TestService)

        assert isinstance(instance, TestService)
        assert instance.name == "test_service"
        assert self.container.is_registered(TestService)
        assert self.container.has(TestService)

    def test_dependency_injection_flow(self):
        """Test complete dependency injection flow."""

        class Repository:
            def __init__(self):
                self.data = "repository_data"

        class Service:
            def __init__(self, repo: Repository):
                self.repository = repo

        class Controller:
            def __init__(self, service: Service):
                self.service = service

        # Register repository as singleton
        self.container.register_singleton(Repository)

        # Resolve controller (should auto-resolve dependencies)
        controller = self.container.get(Controller)

        assert isinstance(controller, Controller)
        assert isinstance(controller.service, Service)
        assert isinstance(controller.service.repository, Repository)
        assert controller.service.repository.data == "repository_data"

    def test_singleton_behavior(self):
        """Test singleton behavior across the container."""

        class SingletonService:
            def __init__(self):
                self.instance_id = id(self)

        self.container.register_singleton(SingletonService)

        # Resolve multiple times
        instance1 = self.container.get(SingletonService)
        instance2 = self.container.get(SingletonService)
        instance3 = self.container.get(SingletonService)

        # All should be the same instance
        assert instance1 is instance2
        assert instance2 is instance3
        assert instance1.instance_id == instance2.instance_id

    def test_factory_registration(self):
        """Test factory registration and resolution."""

        class FactoryProduct:
            def __init__(self, value: str):
                self.value = value

        def product_factory():
            return FactoryProduct("factory_created")

        self.container.register_factory(FactoryProduct, product_factory)

        # Each resolution should create a new instance
        instance1 = self.container.get(FactoryProduct)
        instance2 = self.container.get(FactoryProduct)

        assert isinstance(instance1, FactoryProduct)
        assert isinstance(instance2, FactoryProduct)
        assert instance1 is not instance2  # Different instances
        assert instance1.value == "factory_created"
        assert instance2.value == "factory_created"

    def test_instance_registration(self):
        """Test pre-created instance registration."""

        class PreCreatedService:
            def __init__(self, value: str):
                self.value = value

        pre_instance = PreCreatedService("pre_created")
        self.container.register_instance(PreCreatedService, pre_instance)

        resolved_instance = self.container.get(PreCreatedService)

        assert resolved_instance is pre_instance
        assert resolved_instance.value == "pre_created"

    def test_interface_to_implementation_mapping(self):
        """Test interface to implementation mapping."""

        class IService:
            def get_name(self):
                raise NotImplementedError

        class ConcreteService(IService):
            def get_name(self):
                return "concrete_service"

        self.container.register_type(IService, ConcreteService)

        instance = self.container.get(IService)

        assert isinstance(instance, ConcreteService)
        assert isinstance(instance, IService)
        assert instance.get_name() == "concrete_service"

    def test_cqrs_handler_registration(self):
        """Test CQRS handler registration and resolution."""

        class TestCommand:
            def __init__(self, data: str):
                self.data = data

        class TestQuery:
            def __init__(self, query: str):
                self.query = query

        class TestEvent:
            def __init__(self, event_data: str):
                self.event_data = event_data

        class TestCommandHandler:
            def handle(self, command: TestCommand):
                return f"handled: {command.data}"

        class TestQueryHandler:
            def handle(self, query: TestQuery):
                return f"result: {query.query}"

        class TestEventHandler:
            def handle(self, event: TestEvent):
                return f"processed: {event.event_data}"

        # Register handlers
        self.container.register_command_handler(TestCommand, TestCommandHandler)
        self.container.register_query_handler(TestQuery, TestQueryHandler)
        self.container.register_event_handler(TestEvent, TestEventHandler)

        # Resolve handlers
        command_handler = self.container.get_command_handler(TestCommand)
        query_handler = self.container.get_query_handler(TestQuery)
        event_handlers = self.container.get_event_handlers(TestEvent)

        assert isinstance(command_handler, TestCommandHandler)
        assert isinstance(query_handler, TestQueryHandler)
        assert len(event_handlers) == 1
        assert isinstance(event_handlers[0], TestEventHandler)

        # Test handler functionality
        assert command_handler.handle(TestCommand("test")) == "handled: test"
        assert query_handler.handle(TestQuery("search")) == "result: search"
        assert event_handlers[0].handle(TestEvent("data")) == "processed: data"

    def test_multiple_event_handlers(self):
        """Test multiple event handlers for the same event."""

        class TestEvent:
            def __init__(self, data: str):
                self.data = data

        class EventHandler1:
            def handle(self, event: TestEvent):
                return f"handler1: {event.data}"

        class EventHandler2:
            def handle(self, event: TestEvent):
                return f"handler2: {event.data}"

        class EventHandler3:
            def handle(self, event: TestEvent):
                return f"handler3: {event.data}"

        # Register multiple handlers
        self.container.register_event_handler(TestEvent, EventHandler1)
        self.container.register_event_handler(TestEvent, EventHandler2)
        self.container.register_event_handler(TestEvent, EventHandler3)

        # Resolve all handlers
        handlers = self.container.get_event_handlers(TestEvent)

        assert len(handlers) == 3
        handler_types = [type(h) for h in handlers]
        assert EventHandler1 in handler_types
        assert EventHandler2 in handler_types
        assert EventHandler3 in handler_types

    def test_optional_dependency_resolution(self):
        """Test optional dependency resolution."""

        class OptionalService:
            def __init__(self):
                self.name = "optional"

        # Test get_optional with non-registered service
        optional_instance = self.container.get_optional(OptionalService)
        assert optional_instance is None

        # Register and test again
        self.container.register_singleton(OptionalService)
        optional_instance = self.container.get_optional(OptionalService)
        assert optional_instance is not None
        assert isinstance(optional_instance, OptionalService)
        assert optional_instance.name == "optional"

    def test_get_all_dependencies(self):
        """Test getting all instances of a type."""

        class MultiService:
            def __init__(self):
                self.name = "multi"

        # Test with no registrations
        all_instances = self.container.get_all(MultiService)
        assert len(all_instances) == 0

        # Register and test
        self.container.register_singleton(MultiService)
        all_instances = self.container.get_all(MultiService)
        assert len(all_instances) == 1
        assert isinstance(all_instances[0], MultiService)

    def test_unregister_dependency(self):
        """Test unregistering dependencies."""

        class UnregisterableService:
            def __init__(self):
                self.name = "unregisterable"

        # Register
        self.container.register_singleton(UnregisterableService)
        assert self.container.is_registered(UnregisterableService)

        # Unregister
        result = self.container.unregister(UnregisterableService)
        assert result is True
        assert not self.container.is_registered(UnregisterableService)

        # Unregister non-existent
        result = self.container.unregister(UnregisterableService)
        assert result is False

    def test_clear_container(self):
        """Test clearing the entire container."""

        class Service1:
            pass

        class Service2:
            pass

        class TestCommand:
            pass

        class TestCommandHandler:
            pass

        # Register various things
        self.container.register_singleton(Service1)
        self.container.register_factory(Service2, lambda: Service2())
        self.container.register_command_handler(TestCommand, TestCommandHandler)

        # Verify registrations
        assert self.container.is_registered(Service1)
        assert self.container.is_registered(Service2)

        # Clear container
        self.container.clear()

        # Verify everything is cleared
        assert not self.container.is_registered(Service1)
        assert not self.container.is_registered(Service2)

        stats = self.container.get_stats()
        assert stats["service_registry"]["total_registrations"] == 0
        assert stats["cqrs_registry"]["command_handlers"] == 0

    def test_error_handling(self):
        """Test error handling in the container."""

        class NonExistentService:
            pass

        class FailingService:
            def __init__(self):
                raise ValueError("Service initialization failed")

        # Test resolution of non-existent service
        with pytest.raises(DependencyResolutionError):
            self.container.get(NonExistentService)

        # Test resolution of failing service
        with pytest.raises(DependencyResolutionError):
            self.container.get(FailingService)

        # Test CQRS handler errors
        class NonExistentCommand:
            pass

        with pytest.raises(DependencyResolutionError):
            self.container.get_command_handler(NonExistentCommand)

    def test_circular_dependency_detection(self):
        """Test circular dependency detection."""

        class ServiceA:
            def __init__(self, b: "ServiceB"):
                self.b = b

        class ServiceB:
            def __init__(self, a: ServiceA):
                self.a = a

        with pytest.raises(CircularDependencyError):
            self.container.get(ServiceA)

    def test_performance_monitoring(self):
        """Test performance monitoring functionality."""

        class SlowService:
            def __init__(self):
                import time

                time.sleep(0.01)  # Small delay to trigger timing
                self.name = "slow"

        # This should work and potentially log timing information
        self.container.register_singleton(SlowService)
        instance = self.container.get(SlowService)

        assert isinstance(instance, SlowService)
        assert instance.name == "slow"

    def test_thread_safety(self):
        """Test thread safety of the container."""

        class ThreadSafeService:
            def __init__(self):
                self.thread_id = threading.current_thread().ident

        self.container.register_singleton(ThreadSafeService)

        instances = []
        errors = []

        def resolve_in_thread():
            try:
                instance = self.container.get(ThreadSafeService)
                instances.append(instance)
            except Exception as e:
                errors.append(str(e))

        # Create multiple threads
        threads = []
        for _ in range(10):
            thread = threading.Thread(target=resolve_in_thread)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(instances) == 10

        # All instances should be the same (singleton)
        first_instance = instances[0]
        for instance in instances[1:]:
            assert instance is first_instance

    def test_injectable_class_registration(self):
        """Test injectable class registration."""

        class InjectableService:
            def __init__(self):
                self.name = "injectable"

        # Mock injectable decorator
        with patch(
            "src.infrastructure.di.components.dependency_resolver.is_injectable",
            return_value=True,
        ):
            with patch(
                "src.infrastructure.di.components.dependency_resolver.get_injectable_metadata",
                return_value=None,
            ):
                self.container.register_injectable_class(InjectableService)

                assert self.container.is_registered(InjectableService)
                instance = self.container.get(InjectableService)
                assert isinstance(instance, InjectableService)
                assert instance.name == "injectable"

    def test_get_registrations(self):
        """Test getting all registrations."""

        class Service1:
            pass

        class Service2:
            pass

        self.container.register_singleton(Service1)
        self.container.register_factory(Service2, lambda: Service2())

        registrations = self.container.get_registrations()

        assert len(registrations) >= 2
        assert Service1 in registrations
        assert Service2 in registrations
        assert registrations[Service1].scope == DIScope.SINGLETON
        assert registrations[Service2].scope == DIScope.TRANSIENT


class TestTimedOperation:
    """Test the timed_operation context manager."""

    def test_timed_operation_fast(self):
        """Test timed operation with fast execution."""
        with patch("src.infrastructure.di.container.logger") as mock_logger:
            with timed_operation("fast_operation"):
                pass  # Fast operation

            # Should log debug message for fast operations
            mock_logger.debug.assert_called()
            mock_logger.warning.assert_not_called()

    def test_timed_operation_slow(self):
        """Test timed operation with slow execution."""
        with patch("src.infrastructure.di.container.logger") as mock_logger:
            with timed_operation("slow_operation"):
                import time

                time.sleep(0.11)  # Slow operation (>0.1s)

            # Should log warning for slow operations
            mock_logger.warning.assert_called()

    def test_timed_operation_with_exception(self):
        """Test timed operation when exception occurs."""
        with patch("src.infrastructure.di.container.logger") as mock_logger:
            try:
                with timed_operation("failing_operation"):
                    raise ValueError("Test exception")
            except ValueError:
                pass  # Expected

            # Should still log timing even when exception occurs
            mock_logger.debug.assert_called()


class TestDIContainerIntegration:
    """Integration tests for the complete container system."""

    def setup_method(self):
        """Set up test fixtures."""
        self.container = DIContainer()

    def test_complex_dependency_graph(self):
        """Test resolving a complex dependency graph."""

        class Database:
            def __init__(self):
                self.connection = "db_connection"

        class Repository:
            def __init__(self, db: Database):
                self.database = db

        class Service:
            def __init__(self, repo: Repository):
                self.repository = repo

        class Controller:
            def __init__(self, service: Service):
                self.service = service

        class Application:
            def __init__(self, controller: Controller, db: Database):
                self.controller = controller
                self.database = db

        # Register database as singleton
        self.container.register_singleton(Database)

        # Resolve application
        app = self.container.get(Application)

        # Verify the entire dependency graph
        assert isinstance(app, Application)
        assert isinstance(app.controller, Controller)
        assert isinstance(app.controller.service, Service)
        assert isinstance(app.controller.service.repository, Repository)
        assert isinstance(app.controller.service.repository.database, Database)
        assert isinstance(app.database, Database)

        # Database should be the same singleton instance
        assert app.database is app.controller.service.repository.database
        assert app.database.connection == "db_connection"

    def test_mixed_registration_types(self):
        """Test mixing different registration types."""

        class SingletonService:
            def __init__(self):
                self.type = "singleton"

        class FactoryService:
            def __init__(self, value: str):
                self.value = value

        class InstanceService:
            def __init__(self, name: str):
                self.name = name

        class CompositeService:
            def __init__(
                self,
                singleton: SingletonService,
                factory: FactoryService,
                instance: InstanceService,
            ):
                self.singleton = singleton
                self.factory = factory
                self.instance = instance

        # Register different types
        self.container.register_singleton(SingletonService)
        self.container.register_factory(
            FactoryService, lambda: FactoryService("factory_value"))
        pre_instance = InstanceService("pre_created")
        self.container.register_instance(InstanceService, pre_instance)

        # Resolve composite service
        composite = self.container.get(CompositeService)

        assert isinstance(composite, CompositeService)
        assert composite.singleton.type == "singleton"
        assert composite.factory.value == "factory_value"
        assert composite.instance is pre_instance
        assert composite.instance.name == "pre_created"

    def test_cqrs_with_dependencies(self):
        """Test CQRS handlers with their own dependencies."""

        class Logger:
            def log(self, message: str):
                return f"logged: {message}"

        class TestCommand:
            def __init__(self, data: str):
                self.data = data

        class TestCommandHandler:
            def __init__(self, logger: Logger):
                self.logger = logger

            def handle(self, command: TestCommand):
                return self.logger.log(f"handling {command.data}")

        # Register dependencies
        self.container.register_singleton(Logger)
        self.container.register_command_handler(TestCommand, TestCommandHandler)

        # Resolve and test
        handler = self.container.get_command_handler(TestCommand)
        result = handler.handle(TestCommand("test_data"))

        assert result == "logged: handling test_data"
        assert isinstance(handler.logger, Logger)
