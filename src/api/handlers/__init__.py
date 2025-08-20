"""API handlers package."""

from api.handlers.get_available_templates_handler import (
    GetAvailableTemplatesRESTHandler,
)

__all__: list[str] = ["GetAvailableTemplatesRESTHandler"]
