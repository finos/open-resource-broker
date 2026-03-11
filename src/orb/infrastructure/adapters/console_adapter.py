"""Console adapter using Rich library via orb.cli.console."""

from orb.domain.base.ports.console_port import ConsolePort


class RichConsoleAdapter(ConsolePort):
    """Console adapter using Rich library via orb.cli.console."""

    def info(self, message: str) -> None:
        from orb.cli.console import print_info

        print_info(message)

    def success(self, message: str) -> None:
        from orb.cli.console import print_success

        print_success(message)

    def error(self, message: str) -> None:
        from orb.cli.console import print_error

        print_error(message)

    def warning(self, message: str) -> None:
        from orb.cli.console import print_warning

        print_warning(message)

    def command(self, message: str) -> None:
        from orb.cli.console import print_command

        print_command(message)

    def separator(self, char: str = "-", width: int = 40, color: str = "") -> None:
        from orb.cli.console import print_separator

        print_separator(width=width, char=char, color=color)
