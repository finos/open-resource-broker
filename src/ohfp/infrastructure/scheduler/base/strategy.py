"""Base scheduler strategy interface.

This module provides the base abstract class for all scheduler strategies,
ensuring consistent interface implementation across different scheduler types.
"""

from abc import ABC
from typing import Any

from domain.base.ports.scheduler_port import SchedulerPort


class BaseSchedulerStrategy(SchedulerPort, ABC):
    """Base class for all scheduler strategies.

    This abstract base class defines the common interface and behavior
    that all scheduler strategy implementations must provide.

    Inherits from SchedulerPort which defines all the required abstract methods.
    """

    def __init__(self, config_manager: Any, logger: Any) -> None:
        """Initialize base scheduler strategy.

        Args:
            config_manager: Configuration manager instance
            logger: Logger instance for this strategy
        """
        self.config_manager = config_manager
        self.logger = logger
