"""Scheduler configuration schema."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from orb.config.platform_dirs import get_config_location


class SchedulerConfig(BaseModel):
    """Scheduler configuration - single scheduler like storage strategy."""

    type: Literal["default", "hostfactory"] = Field(
        "default", description="Scheduler type (default, hostfactory)"
    )
    config_root: Optional[str] = Field(
        None, description="Root path for configs (supports $ENV_VAR expansion)"
    )
    config_dir: Optional[str] = Field(None, description="Config directory override")
    work_dir: Optional[str] = Field(None, description="Work directory override")
    log_dir: Optional[str] = Field(None, description="Log directory override")
    log_level: Optional[str] = Field(None, description="Log level override")
    console_enabled: Optional[bool] = Field(None, description="Console logging enabled override")
    templates_filename: Optional[str] = Field(
        None, description="Override templates filename (null = use scheduler default)"
    )

    def get_config_root(self) -> str:
        """Get config root with automatic environment variable expansion."""
        if self.config_root:
            return self.config_root

        return str(get_config_location())
