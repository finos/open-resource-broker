"""Command-line interface configuration schema."""

from pydantic import BaseModel, ConfigDict, Field


class CliConfig(BaseModel):
    """CLI behaviour settings."""

    model_config = ConfigDict(extra="forbid")

    update_check: bool = Field(
        default=False,
        description=(
            "When true, the CLI checks PyPI at most once every 24 hours for a newer "
            "orb-py release and prints one line on stderr after an interactive command. "
            "Off by default: no network request is made unless this is enabled."
        ),
    )
