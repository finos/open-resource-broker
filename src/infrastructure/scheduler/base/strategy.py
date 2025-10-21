"""Base scheduler strategy interface.

This module provides the base abstract class for all scheduler strategies,
ensuring consistent interface implementation across different scheduler types.
"""

from abc import ABC
from typing import Any

from domain.base.ports.scheduler_port import SchedulerPort
from domain.request.aggregate import Request


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

    def format_request_status_response(self, requests: list[Request]) -> dict[str, Any]:
        """
        Format domain Requests to native domain response format.

        Uses the Request's to_dict() method to serialize to native format.
        """
        # KBG TODO requestId for hostfactory returned as request_id, but it does not trigger an error, likely ignored by HF
        return {
            "requests": [request.to_dict() for request in requests],
            "message": "Request status retrieved successfully",
            "count": len(requests),
        }
