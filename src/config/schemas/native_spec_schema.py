"""Native spec configuration schema."""

from typing import Literal

from pydantic import BaseModel, Field


class NativeSpecConfig(BaseModel):
    """Native spec configuration."""

    enabled: bool = Field(False, description="Enable native spec support")
    merge_mode: Literal["extend", "override", "none"] = Field(
        "extend", description="Spec merge mode"
    )
