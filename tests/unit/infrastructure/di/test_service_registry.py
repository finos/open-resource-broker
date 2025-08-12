"""Unit tests for ServiceRegistry component."""

import threading

from src.domain.base.di_contracts import DependencyRegistration, DILifecycle, DIScope
from src.infrastructure.di.components.service_registry import ServiceRegistry


class TestServiceRegistry:
    """Test cases for ServiceRegistry."""

    def setup_method(self):
        """Set up test fixtures."""
        self.registry = ServiceRegistry()

    def test_initialization(self):
        """Test registry initialization."""
        assert self.registry is not None
        assert len(self.registry.get_registrations()) == 0
        assert len(self.registry.get_stats()["scope_types"]) > 0

    def test_register_singleton_class(self):
        """Test registering a singleton class."""

        class TestClass:
            pass

        self.registry.register_singleton(TestClass)

        assert self.registry.is_registered(TestClass)
        assert self.registry.has(TestClass)

        registration = self.registry.get_registration(TestClass)
        assert registration is not None
        assert registration.scope == DIScope.SINGLETON
        assert registration.dependency_type == TestClass

    def test_register_singleton_with_instance(self):
        """Test registering a singleton with pre-created instance."""

        class TestClass:
            def __init__(self, value: str = "test"):
                self.value = value

        instance = TestClass("singleton_instance")
        self.registry.register_singleton(TestClass, instance)

        assert self.registry.is_registered(TestClass)
        cached_instance = self.registry.get_singleton_instance(TestClass)
        assert cached_instance is instance
        assert cached_instance.value == "singleton_instance"

    def test_register_singleton_with_factory(self):
        """Test registering a singleton with factory function."""

        class TestClass:
            def __init__(self, value: str):
                self.value = value

        def factory(container):
            return TestClass("factory_created")

        self.registry.register_singleton(TestClass, factory)

        assert self.registry.is_registered(TestClass)
        registration = self.registry.get_registration(TestClass)
        assert registration.factory is factory

    def test_register_factory(self):
        """Test registering a factory function."""

        class TestClass:
            pass

        def factory():
            return TestClass()

        self.registry.register_factory(TestClass, factory)

        assert self.registry.is_registered(TestClass)
        registration = self.registry.get_registration(TestClass)
        assert registration.scope == DIScope.TRANSIENT
        assert registration.factory is factory

    def test_register_instance(self):
        """Test registering a specific instance."""

        class TestClass:
            def __init__(self, value: str):
                self.value = value

        instance = TestClass("test_instance")
        self.registry.register_instance(TestClass, instance)

        assert self.registry.is_registered(TestClass)
        registration = self.registry.get_registration(TestClass)
        assert registration.scope == DIScope.SINGLETON
        assert registration.instance is instance

        cached_instance = self.registry.get_singleton_instance(TestClass)
        assert cached_instance is instance

    def test_register_type_mapping(self):
        """Test registering interface to implementation mapping."""

        class IInterface:
            pass

        class Implementation(IInterface):
            pass

        self.registry.register_type(IInterface, Implementation, DIScope.TRANSIENT)

        assert self.registry.is_registered(IInterface)
        registration = self.registry.get_registration(IInterface)
        assert registration.dependency_type == IInterface
        assert registration.implementation_type == Implementation
        assert registration.scope == DIScope.TRANSIENT

    def test_register_dependency_registration(self):
        """Test registering with DependencyRegistration object."""

        class TestClass:
            pass

        registration = DependencyRegistration(
            dependency_type=TestClass,
            scope=DIScope.SINGLETON,
            implementation_type=TestClass,
            lifecycle=DILifecycle.EAGER,
        )

        self.registry.register(registration)

        assert self.registry.is_registered(TestClass)
        retrieved_registration = self.registry.get_registration(TestClass)
        assert retrieved_registration.scope == DIScope.SINGLETON
        assert retrieved_registration.lifecycle == DILifecycle.EAGER

    def test_register_injectable_class(self):
        """Test registering injectable class."""

        class TestClass:
            pass

        self.registry.register_injectable_class(TestClass)

        assert self.registry.is_registered(TestClass)
        registration = self.registry.get_registration(TestClass)
        assert registration.dependency_type == TestClass
        assert registration.implementation_type == TestClass

    def test_unregister(self):
        """Test unregistering a dependency."""

        class TestClass:
            pass

        self.registry.register_singleton(TestClass)
        assert self.registry.is_registered(TestClass)

        result = self.registry.unregister(TestClass)
        assert result is True
        assert not self.registry.is_registered(TestClass)

        # Unregistering non-existent type should return False
        result = self.registry.unregister(TestClass)
        assert result is False

    def test_clear(self):
        """Test clearing all registrations."""

        class TestClass1:
            pass

        class TestClass2:
            pass

        self.registry.register_singleton(TestClass1)
        self.registry.register_factory(TestClass2, lambda: TestClass2())

        assert self.registry.is_registered(TestClass1)
        assert self.registry.is_registered(TestClass2)

        self.registry.clear()

        assert not self.registry.is_registered(TestClass1)
        assert not self.registry.is_registered(TestClass2)
        assert len(self.registry.get_registrations()) == 0

    def test_singleton_instance_caching(self):
        """Test singleton instance caching."""

        class TestClass:
            def __init__(self, value: str = "default"):
                self.value = value

        instance = TestClass("cached")
        self.registry.set_singleton_instance(TestClass, instance)

        retrieved_instance = self.registry.get_singleton_instance(TestClass)
        assert retrieved_instance is instance
        assert retrieved_instance.value == "cached"

    def test_get_stats(self):
        """Test getting registry statistics."""

        class TestClass1:
            pass

        class TestClass2:
            pass

        class TestClass3:
            pass

        self.registry.register_singleton(TestClass1)
        self.registry.register_factory(TestClass2, lambda: TestClass2())
        self.registry.register_injectable_class(TestClass3)

        stats = self.registry.get_stats()

        assert "total_registrations" in stats
        assert "singleton_instances" in stats
        assert "injectable_classes" in stats
        assert "scope_types" in stats

        assert stats["total_registrations"] == 3
        assert stats["injectable_classes"] == 1
        assert DIScope.SINGLETON.value in stats["scope_types"]
        assert DIScope.TRANSIENT.value in stats["scope_types"]

    def test_thread_safety(self):
        """Test thread safety of registry operations."""

        class TestClass:
            def __init__(self, value: int):
                self.value = value

        results = []
        errors = []

        def register_and_retrieve(thread_id: int):
            try:
                # Register with thread-specific instance
                instance = TestClass(thread_id)
                self.registry.register_instance(f"TestClass_{thread_id}", instance)

                # Retrieve and verify
                retrieved = self.registry.get_singleton_instance(
                    f"TestClass_{thread_id}"
                )
                if retrieved and retrieved.value == thread_id:
                    results.append(thread_id)
                else:
                    errors.append(
                        f"Thread {thread_id}: Expected {thread_id}, got {retrieved.value if retrieved else None}"
                    )
            except Exception as e:
                errors.append(f"Thread {thread_id}: {str(e)}")

        # Create multiple threads
        threads = []
        for i in range(10):
            thread = threading.Thread(target=register_and_retrieve, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(results) == 10
        assert sorted(results) == list(range(10))

    def test_registration_overwrite(self):
        """Test that registering same type overwrites previous registration."""

        class TestClass:
            def __init__(self, value: str = "default"):
                self.value = value

        # First registration
        instance1 = TestClass("first")
        self.registry.register_instance(TestClass, instance1)

        retrieved1 = self.registry.get_singleton_instance(TestClass)
        assert retrieved1.value == "first"

        # Second registration should overwrite
        instance2 = TestClass("second")
        self.registry.register_instance(TestClass, instance2)

        retrieved2 = self.registry.get_singleton_instance(TestClass)
        assert retrieved2.value == "second"
        assert retrieved2 is not retrieved1

    def test_get_registration_nonexistent(self):
        """Test getting registration for non-existent type."""

        class NonExistentClass:
            pass

        registration = self.registry.get_registration(NonExistentClass)
        assert registration is None

    def test_get_singleton_instance_nonexistent(self):
        """Test getting singleton instance for non-existent type."""

        class NonExistentClass:
            pass

        instance = self.registry.get_singleton_instance(NonExistentClass)
        assert instance is None


class TestServiceRegistryEdgeCases:
    """Test edge cases and error conditions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.registry = ServiceRegistry()

    def test_register_none_values(self):
        """Test registering with None values."""

        class TestClass:
            pass

        # This should work - None means register the class itself
        self.registry.register_singleton(TestClass, None)
        assert self.registry.is_registered(TestClass)

    def test_multiple_scope_types(self):
        """Test registering different scope types."""

        class SingletonClass:
            pass

        class TransientClass:
            pass

        self.registry.register_type(SingletonClass, SingletonClass, DIScope.SINGLETON)
        self.registry.register_type(TransientClass, TransientClass, DIScope.TRANSIENT)

        singleton_reg = self.registry.get_registration(SingletonClass)
        transient_reg = self.registry.get_registration(TransientClass)

        assert singleton_reg.scope == DIScope.SINGLETON
        assert transient_reg.scope == DIScope.TRANSIENT

        stats = self.registry.get_stats()
        assert stats["scope_types"][DIScope.SINGLETON.value] >= 1
        assert stats["scope_types"][DIScope.TRANSIENT.value] >= 1
