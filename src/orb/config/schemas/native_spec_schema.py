"""Native spec configuration schema."""

from typing import Literal

from pydantic import BaseModel, Field


class NativeSpecConfig(BaseModel):
    """Native spec configuration."""

    enabled: bool = Field(False, description="Enable native spec support")
    merge_mode: Literal["merge", "replace"] = Field(
        "merge",
        description="Spec merge mode: 'merge' combines native spec with default template, 'replace' uses only native spec",
    )
