"""Domain port for querying Auto Scaling Group state."""

from abc import ABC, abstractmethod
from typing import Any


class ASGQueryPort(ABC):
    """Port for querying Auto Scaling Group details from a provider."""

    @abstractmethod
    async def get_asg_details(self, asg_name: str) -> dict[str, Any]:
        """Return current details for the named ASG, or an empty dict if not found."""
