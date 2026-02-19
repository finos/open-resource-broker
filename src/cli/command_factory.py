"""CLI Command Factory - Backward compatibility wrapper around orchestrator."""

from cli.factories import CLICommandFactoryOrchestrator


class CLICommandFactory:
    """Backward compatibility wrapper around CLICommandFactoryOrchestrator."""

    def __init__(self):
        self._orchestrator = CLICommandFactoryOrchestrator()

    def __getattr__(self, name):
        """Delegate all method calls to the orchestrator."""
        return getattr(self._orchestrator, name)


# Global instance for backward compatibility
cli_command_factory = CLICommandFactory()
