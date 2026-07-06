"""Reflex UI configuration schema.

Controls whether the Reflex-based web UI is enabled, and how it is
deployed alongside the REST API.
"""

from pydantic import BaseModel, ConfigDict, Field


class UIConfig(BaseModel):
    """Web UI (Reflex) configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        True,
        description=(
            "Enable the Reflex web UI. When True and ``mode`` is "
            "``embedded``, the UI is served by the same Reflex backend "
            "process that hosts ORB's REST API. Requires the optional "
            "``ui`` extra (``pip install orb-py[ui]``)."
        ),
    )
    mode: str = Field(
        "embedded",
        description=(
            "UI deployment mode. ``embedded`` mounts ORB's FastAPI app "
            "into the Reflex backend (single process, single port). "
            "``remote`` runs Reflex separately and talks to a remote ORB "
            "over HTTP via the ``base_url``."
        ),
        pattern="^(embedded|remote)$",
    )
    backend_port: int = Field(
        8001,
        description="Port for the Reflex backend (embedded mode hosts the ORB API on this port too).",
    )
    frontend_port: int = Field(
        3000,
        description="Port for the Reflex frontend dev/build server.",
    )
    base_url: str = Field(
        "http://localhost:8000",
        description="Remote ORB API base URL (used only when ``mode=remote``).",
    )
