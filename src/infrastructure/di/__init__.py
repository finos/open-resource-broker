"""Dependency Injection package."""

from .container import DIContainer, get_container, reset_container
from .services import create_handler, register_all_services

__all__: list[str] = [
    "DIContainer",
    "get_container",
    "reset_container",
    "register_all_services",
    "create_handler",
]
