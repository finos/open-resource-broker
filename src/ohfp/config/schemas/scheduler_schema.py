"""Scheduler configuration schema."""

from typing import Optional

from pydantic import BaseModel, Field


class SchedulerConfig(BaseModel):
    """Scheduler configuration - single scheduler like storage strategy."""

    type: str = Field("hostfactory", description="Scheduler type (hostfactory, hf)")
    config_root: Optional[str] = Field(
        None, description="Root path for configs (supports $ENV_VAR expansion)"
    )

    def get_config_root(self) -> str:
        """Get config root with automatic environment variable expansion."""
        if self.config_root:
            return self.config_root

        return "config"
