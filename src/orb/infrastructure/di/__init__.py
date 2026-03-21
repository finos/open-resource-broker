"""Dependency Injection package."""

from .container import DIContainer, get_container, reset_container

__all__: list[str] = [
    "DIContainer",
    "get_container",
    "reset_container",
]
