"""Unit tests for DependencyResolver component."""

import threading

import pytest

from src.domain.base.di_contracts import DependencyRegistration, DILifecycle, DIScope
from src.infrastructure.di.components.cqrs_registry import CQRSHandlerRegistry
from src.infrastructure.di.components.dependency_resolver import DependencyResolver
from src.infrastructure.di.components.service_registry import ServiceRegistry
from src.infrastructure.di.exceptions import (
    CircularDependencyError,
    DependencyResolutionError,
    FactoryError,
    InstantiationError,
    UntypedParameterError,
)


class TestDependencyResolver:
    """Test cases for DependencyResolver."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service_registry = ServiceRegistry()
        self.cqrs_registry = CQRSHandlerRegistry()
        self.resolver = DependencyResolver(self.service_registry, self.cqrs_registry)

    def test_initialization(self):
        """Test resolver initialization."""
        assert self.resolver is not None
        assert self.resolver._service_registry is self.service_registry
        assert self.resolver._cqrs_registry is self.cqrs_registry

    def test_resolve_simple_class(self):
        """Test resolving a simple class with no dependencies."""

        class SimpleClass:
            def __init__(self):
                self.value = "simple"

        instance = self.resolver.resolve(SimpleClass)

        assert isinstance(instance, SimpleClass)
        assert instance.value == "simple"

    def test_resolve_class_with_dependencies(self):
        """Test resolving a class with dependencies."""

        class Dependency:
            def __init__(self):
                self.name = "dependency"

        class MainClass:
            def __init__(self, dep: Dependency):
                self.dependency = dep

        instance = self.resolver.resolve(MainClass)

        assert isinstance(instance, MainClass)
        assert isinstance(instance.dependency, Dependency)
        assert instance.dependency.name == "dependency"

    def test_resolve_registered_singleton(self):
        """Test resolving a registered singleton."""

        class SingletonClass:
            def __init__(self):
                self.value = "singleton"

        # Register as singleton
        self.service_registry.register_singleton(SingletonClass)

        # Resolve twice
        instance1 = self.resolver.resolve(SingletonClass)
        instance2 = self.resolver.resolve(SingletonClass)

        # Should be the same instance
        assert instance1 is instance2
        assert instance1.value == "singleton"

    def test_resolve_registered_factory(self):
        """Test resolving with a factory function."""

        class FactoryClass:
            def __init__(self, value: str):
                self.value = value

        def factory():
            return FactoryClass("from_factory")

        self.service_registry.register_factory(FactoryClass, factory)

        instance = self.resolver.resolve(FactoryClass)

        assert isinstance(instance, FactoryClass)
        assert instance.value == "from_factory"

    def test_resolve_registered_instance(self):
        """Test resolving a pre-registered instance."""

        class InstanceClass:
            def __init__(self, value: str):
                self.value = value

        pre_instance = InstanceClass("pre_registered")
        self.service_registry.register_instance(InstanceClass, pre_instance)

        instance = self.resolver.resolve(InstanceClass)

        assert instance is pre_instance
        assert instance.value == "pre_registered"

    def test_resolve_interface_to_implementation(self):
        """Test resolving interface to implementation mapping."""

        class IInterface:
            pass

        class Implementation(IInterface):
            def __init__(self):
                self.type = "implementation"

        self.service_registry.register_type(IInterface, Implementation)

        instance = self.resolver.resolve(IInterface)

        assert isinstance(instance, Implementation)
        assert isinstance(instance, IInterface)
        assert instance.type == "implementation"

    def test_circular_dependency_detection(self):
        """Test circular dependency detection."""

        class ClassA:
            def __init__(self, b: "ClassB"):
                self.b = b

        class ClassB:
            def __init__(self, a: ClassA):
                self.a = a

        with pytest.raises(CircularDependencyError) as exc_info:
            self.resolver.resolve(ClassA)

        assert "Circular dependency detected" in str(exc_info.value)

    def test_untyped_parameter_error(self):
        """Test error handling for untyped parameters."""

        class UntypedClass:
            def __init__(self, untyped_param):  # No type annotation
                self.param = untyped_param

        with pytest.raises(UntypedParameterError) as exc_info:
            self.resolver.resolve(UntypedClass)

        assert "has no type annotation" in str(exc_info.value)

    def test_optional_parameters(self):
        """Test handling of optional parameters with defaults."""

        class OptionalClass:
            def __init__(self, required: str, optional: str = "default"):
                self.required = required
                self.optional = optional

        # This should fail because 'required' has no default and can't be resolved
        with pytest.raises(DependencyResolutionError):
            self.resolver.resolve(OptionalClass)

    def test_factory_with_dependencies(self):
        """Test factory function that has its own dependencies."""

        class Dependency:
            def __init__(self):
                self.name = "dep"

        class FactoryProduct:
            def __init__(self, value: str, dep: Dependency):
                self.value = value
                self.dependency = dep

        def factory_with_deps(dep: Dependency):
            return FactoryProduct("factory_made", dep)

        self.service_registry.register_factory(FactoryProduct, factory_with_deps)

        instance = self.resolver.resolve(FactoryProduct)

        assert isinstance(instance, FactoryProduct)
        assert instance.value == "factory_made"
        assert isinstance(instance.dependency, Dependency)
        assert instance.dependency.name == "dep"

    def test_factory_error_handling(self):
        """Test error handling when factory function fails."""

        class FactoryClass:
            pass

        def failing_factory():
            raise ValueError("Factory failed")

        self.service_registry.register_factory(FactoryClass, failing_factory)

        with pytest.raises(FactoryError) as exc_info:
            self.resolver.resolve(FactoryClass)

        assert "Factory failed" in str(exc_info.value)

    def test_injectable_auto_registration(self):
        """Test auto-registration of injectable classes."""
        # Mock the injectable decorator functions
        from unittest.mock import patch

        class InjectableClass:
            def __init__(self):
                self.value = "injectable"

        with patch(
            "src.infrastructure.di.components.dependency_resolver.is_injectable",
            return_value=True,
        ):
            with patch(
                "src.infrastructure.di.components.dependency_resolver.get_injectable_metadata",
                return_value=None,
            ):
                instance = self.resolver.resolve(InjectableClass)

                assert isinstance(instance, InjectableClass)
                assert instance.value == "injectable"
                # Should be auto-registered
                assert self.service_registry.is_registered(InjectableClass)

    def test_string_annotation_resolution(self):
        """Test resolution of string type annotations."""

        # This is tricky to test directly, but we can test the method
        class TestClass:
            pass

        # Test basic string annotation resolution
        resolved_type = self.resolver._resolve_string_annotation("str", TestClass)
        assert resolved_type == str

        resolved_type = self.resolver._resolve_string_annotation("int", TestClass)
        assert resolved_type == int

    def test_clear_cache(self):
        """Test clearing the resolution cache."""

        class CacheableClass:
            def __init__(self):
                self.value = "cached"

        # This should work without errors
        self.resolver.clear_cache()

        # Resolve after clearing cache
        instance = self.resolver.resolve(CacheableClass)
        assert isinstance(instance, CacheableClass)

    def test_complex_dependency_chain(self):
        """Test resolving a complex chain of dependencies."""

        class Level1:
            def __init__(self):
                self.level = 1

        class Level2:
            def __init__(self, l1: Level1):
                self.level = 2
                self.dependency = l1

        class Level3:
            def __init__(self, l2: Level2):
                self.level = 3
                self.dependency = l2

        class Level4:
            def __init__(self, l3: Level3):
                self.level = 4
                self.dependency = l3

        instance = self.resolver.resolve(Level4)

        assert instance.level == 4
        assert instance.dependency.level == 3
        assert instance.dependency.dependency.level == 2
        assert instance.dependency.dependency.dependency.level == 1

    def test_thread_safety(self):
        """Test thread safety of dependency resolution."""

        class ThreadSafeClass:
            def __init__(self):
                self.thread_id = threading.current_thread().ident

        results = []
        errors = []

        def resolve_in_thread():
            try:
                instance = self.resolver.resolve(ThreadSafeClass)
                results.append(instance.thread_id)
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
        assert len(results) == 10
        # Each thread should have resolved its own instance
        assert len(set(results)) == 10  # All different thread IDs

    def test_singleton_thread_safety(self):
        """Test that singletons are properly shared across threads."""

        class SingletonClass:
            def __init__(self):
                self.created_at = threading.current_thread().ident

        self.service_registry.register_singleton(SingletonClass)

        instances = []
        errors = []

        def resolve_singleton():
            try:
                instance = self.resolver.resolve(SingletonClass)
                instances.append(instance)
            except Exception as e:
                errors.append(str(e))

        # Create multiple threads
        threads = []
        for _ in range(10):
            thread = threading.Thread(target=resolve_singleton)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(instances) == 10
        # All instances should be the same object
        first_instance = instances[0]
        for instance in instances[1:]:
            assert instance is first_instance


class TestDependencyResolverEdgeCases:
    """Test edge cases and error conditions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service_registry = ServiceRegistry()
        self.cqrs_registry = CQRSHandlerRegistry()
        self.resolver = DependencyResolver(self.service_registry, self.cqrs_registry)

    def test_resolve_with_none_type(self):
        """Test behavior when trying to resolve None type."""
        with pytest.raises((DependencyResolutionError, AttributeError)):
            self.resolver.resolve(None)

    def test_resolve_builtin_types(self):
        """Test resolving built-in types (should fail)."""
        with pytest.raises(DependencyResolutionError):
            self.resolver.resolve(str)

        with pytest.raises(DependencyResolutionError):
            self.resolver.resolve(int)

    def test_resolve_abstract_class(self):
        """Test resolving abstract classes."""
        from abc import ABC, abstractmethod

        class AbstractClass(ABC):
            @abstractmethod
            def abstract_method(self):
                pass

        with pytest.raises((DependencyResolutionError, InstantiationError)):
            self.resolver.resolve(AbstractClass)

    def test_dependency_chain_tracking(self):
        """Test that dependency chain is properly tracked."""

        class ClassA:
            def __init__(self, b: "ClassB"):
                self.b = b

        class ClassB:
            def __init__(self, c: "ClassC"):
                self.c = c

        class ClassC:
            def __init__(self, a: ClassA):  # Creates circular dependency
                self.a = a

        with pytest.raises(CircularDependencyError) as exc_info:
            self.resolver.resolve(ClassA)

        error_message = str(exc_info.value)
        assert "ClassA" in error_message
        assert "ClassB" in error_message
        assert "ClassC" in error_message

    def test_factory_with_no_parameters(self):
        """Test factory function with no parameters."""

        class SimpleProduct:
            def __init__(self):
                self.value = "simple"

        def simple_factory():
            return SimpleProduct()

        self.service_registry.register_factory(SimpleProduct, simple_factory)

        instance = self.resolver.resolve(SimpleProduct)
        assert isinstance(instance, SimpleProduct)
        assert instance.value == "simple"

    def test_registration_with_complex_lifecycle(self):
        """Test registration with complex lifecycle settings."""

        class ComplexClass:
            def __init__(self):
                self.value = "complex"

        registration = DependencyRegistration(
            dependency_type=ComplexClass,
            implementation_type=ComplexClass,
            scope=DIScope.SINGLETON,
            lifecycle=DILifecycle.EAGER,
        )

        self.service_registry.register(registration)

        instance = self.resolver.resolve(ComplexClass)
        assert isinstance(instance, ComplexClass)
        assert instance.value == "complex"

    def test_error_propagation(self):
        """Test that errors are properly propagated through the resolution chain."""

        class FailingClass:
            def __init__(self):
                raise ValueError("Intentional failure")

        class DependentClass:
            def __init__(self, failing: FailingClass):
                self.failing = failing

        with pytest.raises(InstantiationError) as exc_info:
            self.resolver.resolve(DependentClass)

        # The error should mention the failing class
        assert "FailingClass" in str(exc_info.value) or "Intentional failure" in str(exc_info.value)


class TestDependencyResolverIntegration:
    """Integration tests with other components."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service_registry = ServiceRegistry()
        self.cqrs_registry = CQRSHandlerRegistry()
        self.resolver = DependencyResolver(self.service_registry, self.cqrs_registry)

    def test_integration_with_service_registry(self):
        """Test integration with ServiceRegistry."""

        class ServiceClass:
            def __init__(self):
                self.name = "service"

        class ClientClass:
            def __init__(self, service: ServiceClass):
                self.service = service

        # Register service as singleton
        self.service_registry.register_singleton(ServiceClass)

        # Resolve client (should get the singleton service)
        client1 = self.resolver.resolve(ClientClass)
        client2 = self.resolver.resolve(ClientClass)

        assert client1.service is client2.service  # Same singleton instance
        assert client1.service.name == "service"

    def test_integration_with_cqrs_registry(self):
        """Test integration with CQRSHandlerRegistry."""

        # This is more of a structural test since CQRS integration
        # is handled at a higher level
        class TestCommand:
            pass

        class TestCommandHandler:
            def handle(self, command: TestCommand):
                return "handled"

        self.cqrs_registry.register_command_handler(TestCommand, TestCommandHandler)

        # The resolver should be able to resolve the handler
        handler = self.resolver.resolve(TestCommandHandler)
        assert isinstance(handler, TestCommandHandler)

        # Verify CQRS registry integration
        assert self.cqrs_registry.has_command_handler(TestCommand)
        handler_type = self.cqrs_registry.get_command_handler_type(TestCommand)
        assert handler_type == TestCommandHandler
