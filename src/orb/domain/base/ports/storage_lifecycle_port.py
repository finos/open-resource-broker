"""Storage lifecycle port - focused interface for lifecycle operations."""

from abc import ABC, abstractmethod


class StorageLifecyclePort(ABC):
    """Focused port for storage lifecycle operations.

    This interface follows ISP by providing only lifecycle management operations,
    allowing clients that need resource cleanup to depend on a minimal interface.
    """

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up storage resources.

        This should be called when storage is no longer needed to ensure
        proper resource cleanup (connections, file handles, etc.).
        """
