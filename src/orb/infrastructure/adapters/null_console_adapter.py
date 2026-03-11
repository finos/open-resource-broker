"""No-op console adapter for non-interactive contexts.

Use as a safe default when ConsolePort is optional (inject explicitly).
Do not register in DI — the container always registers RichConsoleAdapter.
"""

from orb.domain.base.ports.console_port import ConsolePort


class NullConsoleAdapter(ConsolePort):
    """No-op console adapter for non-interactive contexts."""

    def info(self, message: str) -> None:
        pass

    def success(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def command(self, message: str) -> None:
        pass

    def separator(self, char: str = "-", width: int = 40, color: str = "") -> None:
        pass
