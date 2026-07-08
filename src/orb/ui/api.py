"""ORB client facade — thin shim over api_http.

The UI always communicates with ORB over HTTP.  In embedded mode the Reflex
backend mounts ORB's FastAPI app at ``/orb`` (via ``api_transformer`` in
``app.py``), so loopback HTTP calls to ``http://localhost:8000/orb/api/v1/…``
work without a separate process.  In remote/standalone mode point
``ORB_BASE_URL`` at the ORB host (default ``http://localhost:8000``).

Pages import from ``api`` only, never from ``api_http`` directly.
"""

from __future__ import annotations

from .api_http import (
    batch_get_request_status,
    cancel_request,
    create_template,
    delete_template,
    generate_templates,
    get_config,
    get_config_sources,
    get_config_value,
    get_dashboard_summary,
    get_health,
    get_info,
    get_machine,
    get_me,
    get_provider_schemas,
    get_request,
    get_template,
    init_orb,
    list_machines,
    list_requests,
    list_return_requests,
    list_templates,
    refresh_templates,
    reload_config,
    request_machines,
    return_machines,
    set_config_value,
    subscribe_events,
    sync_machine,
    update_template,
    validate_template,
    wipe_database,
)

__all__ = [
    "batch_get_request_status",
    "cancel_request",
    "create_template",
    "delete_template",
    "generate_templates",
    "get_config",
    "get_config_sources",
    "get_config_value",
    "get_dashboard_summary",
    "get_health",
    "get_info",
    "get_machine",
    "get_me",
    "get_provider_schemas",
    "get_request",
    "get_template",
    "init_orb",
    "list_machines",
    "list_requests",
    "list_return_requests",
    "list_templates",
    "refresh_templates",
    "reload_config",
    "request_machines",
    "return_machines",
    "set_config_value",
    "subscribe_events",
    "sync_machine",
    "update_template",
    "validate_template",
    "wipe_database",
    "mode",
]


def mode() -> str:
    """Return the active transport mode.  Always ``'http'``."""
    return "http"
