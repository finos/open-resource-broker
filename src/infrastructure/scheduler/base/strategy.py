"""Base scheduler strategy interface.

This module provides the base abstract class for all scheduler strategies,
ensuring consistent interface implementation across different scheduler types.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from src.domain.base.ports.scheduler_port import SchedulerPort


class BaseSchedulerStrategy(SchedulerPort, ABC):
    """Base class for all scheduler strategies.

    This abstract base class defines the common interface and behavior
    that all scheduler strategy implementations must provide.
    """

    def __init__(self, config_manager: Any, logger: Any):
        """Initialize base scheduler strategy.

        Args:
            config_manager: Configuration manager instance
            logger: Logger instance for this strategy
        """
        self.config_manager = config_manager
        self.logger = logger

    @abstractmethod
    def get_templates(self) -> List[Dict[str, Any]]:
        """Get available templates from the scheduler.

        Returns:
            List of template dictionaries
        """

    @abstractmethod
    def format_output(self, data: Any, output_format: str = "json") -> str:
        """Format output data for the scheduler.

        Args:
            data: Data to format
            output_format: Desired output format

        Returns:
            Formatted output string
        """
