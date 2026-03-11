"""Port for user-facing console output."""

from abc import ABC, abstractmethod


class ConsolePort(ABC):
    """Port for user-facing console output."""

    @abstractmethod
    def info(self, message: str) -> None:
        pass

    @abstractmethod
    def success(self, message: str) -> None:
        pass

    @abstractmethod
    def error(self, message: str) -> None:
        pass

    @abstractmethod
    def warning(self, message: str) -> None:
        pass

    @abstractmethod
    def command(self, message: str) -> None:
        pass

    @abstractmethod
    def separator(self, char: str = "-", width: int = 40, color: str = "") -> None:
        pass
