"""Singleton registry implementation."""

import threading
from typing import Any, Optional, TypeVar, cast

T = TypeVar("T")


class SingletonRegistry:
    """
    Central registry for singleton instances.

    This class provides a centralized registry for all singleton instances
    in the application. It ensures that only one instance of each singleton
    class is created and reused throughout the application.

    The registry itself is a singleton, accessible via the get_instance() method.
    """

    _instance: Optional["SingletonRegistry"] = None
    _lock = threading.RLock()

    def __init__(self) -> None:
        """
        Initialize the singleton registry.

        Note: This constructor should not be called directly.
        Use get_instance() instead.
        """
        self._singletons: dict[type[Any], Any] = {}
        self._locks: dict[type[Any], threading.RLock] = {}

    @classmethod
    def get_instance(cls) -> "SingletonRegistry":
        """
        Get the singleton instance of the registry.

        Returns:
            SingletonRegistry: The singleton instance
        """
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def register(self, singleton_class: type[T], instance: Optional[T] = None) -> None:
        """
        Register a singleton class or instance.

        Args:
            singleton_class: The class to register
            instance: Optional instance to register. If None, the instance
                      will be created when get() is called.
        """
        with self._lock:
            self._singletons[singleton_class] = instance
            if singleton_class not in self._locks:
                self._locks[singleton_class] = threading.RLock()

    def get(self, singleton_class: type[T], *args: Any, **kwargs: Any) -> T:
        """
        Get a singleton instance.

        If the instance doesn't exist yet, it will be created using the
        provided args and kwargs.

        Args:
            singleton_class: The class to get an instance of
            *args: Arguments to pass to the constructor if creating a new instance
            **kwargs: Keyword arguments to pass to the constructor if creating a new instance

        Returns:
            The singleton instance
        """
        # Register the class if it's not registered yet
        if singleton_class not in self._singletons:
            self.register(singleton_class)

        # Get the lock for this singleton class
        lock = self._locks[singleton_class]

        # Create the instance if it doesn't exist yet
        with lock:
            instance = self._singletons[singleton_class]
            if instance is None:
                instance = singleton_class(*args, **kwargs)
                self._singletons[singleton_class] = instance

            return cast(T, instance)

    def reset(self, singleton_class: Optional[type[Any]] = None) -> None:
        """
        Reset one or all singleton instances.

        This method is primarily intended for testing purposes.

        Args:
            singleton_class: The class to reset. If None, all singletons will be reset.
        """
        with self._lock:
            if singleton_class is None:
                # Reset all singletons
                self._singletons = {}
                self._locks = {}
            elif singleton_class in self._singletons:
                # Reset a specific singleton
                self._singletons[singleton_class] = None

    def get_all(self) -> dict[type[Any], Any]:
        """
        Get all registered singletons.

        Returns:
            Dictionary mapping singleton classes to their instances
        """
        with self._lock:
            # Return a copy of the singletons dictionary to avoid modification during
            # iteration
            return dict(self._singletons)
