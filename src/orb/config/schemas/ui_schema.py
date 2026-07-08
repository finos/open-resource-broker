"""Reflex UI configuration schema.

Controls whether the Reflex-based web UI is enabled, and how it is
deployed alongside the REST API.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class UIConfig(BaseModel):
    """Web UI (Reflex) configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        True,
        description=(
            "Top-level toggle for the web UI. When ``False``, no UI serving "
            "occurs regardless of ``mode``; the server behaves as if "
            "``mode='split'``. Requires the optional ``ui`` extra "
            "(``pip install orb-py[ui]``) when ``True``."
        ),
    )
    mode: Literal["embedded", "split", "dev"] = Field(
        "embedded",
        description=(
            "UI runtime mode.\n\n"
            "``embedded`` (default): a single uvicorn serves both the API and "
            "the pre-built static SPA bundle. Requires the [ui] extra and a "
            "wheel that ships the _static/ directory (or a local "
            "``make ui-build``). ``backend_port`` and ``frontend_port`` are "
            "ignored in this mode — the port is taken from "
            "``server_config.port``.\n\n"
            "``split``: API-only uvicorn; the UI is served externally "
            "(nginx/CDN). No static mount inside FastAPI. Only "
            "``server_config.port`` matters; ``backend_port`` and "
            "``frontend_port`` are ignored.\n\n"
            "``dev``: spawn ``reflex run`` for local iteration (requires "
            "Node/Bun). ``backend_port`` and ``frontend_port`` are used to "
            "configure the reflex subprocess."
        ),
    )
    backend_port: int = Field(
        8001,
        description=(
            "Port for the Reflex backend subprocess. "
            "Only used when ``mode='dev'``; ignored in ``embedded`` and "
            "``split`` modes (those modes use ``server_config.port``)."
        ),
    )
    frontend_port: int = Field(
        3000,
        description=(
            "Port for the Reflex frontend dev/build server. "
            "Only used when ``mode='dev'``; ignored in ``embedded`` and "
            "``split`` modes."
        ),
    )
    base_url: Optional[str] = Field(
        None,
        description=(
            "Base URL of the ORB API, used when constructing absolute links "
            "or cross-origin requests. When ``None`` (default), the value is "
            "computed at runtime from ``server_config`` "
            "(e.g. ``http://localhost:<port>``). Set explicitly only when the "
            "server sits behind a reverse proxy or is accessed via a "
            "non-default hostname."
        ),
    )
    static_dir_override: Optional[str] = Field(
        None,
        description=(
            "Absolute path to the pre-built SPA static bundle directory. "
            "When ``None`` (default), the runtime resolves the bundle to "
            "``<orb.ui package>/_static/``. Override only for advanced "
            "scenarios such as testing against a local build or running from "
            "an editable install. Has no effect when ``mode`` is not "
            "``embedded``."
        ),
    )
