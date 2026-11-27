"""Tests for the flat CLI command registry."""


def test_system_reload_resolves_to_handler():
    from orb.cli.registry import build_registry, lookup

    build_registry()
    handler = lookup("system", "reload")
    assert handler is not None


def test_request_status_singular_resolves():
    from orb.cli.registry import build_registry, lookup

    build_registry()
    h1 = lookup("requests", "status")
    h2 = lookup("request", "status")  # alias
    assert h1 is not None
    assert h1 is h2


def test_templates_validate_resolves_to_dedicated_handler():
    from orb.cli.registry import build_registry, lookup
    from orb.interface.template_command_handlers import TemplateReadHandlers

    build_registry()
    handler = lookup("templates", "validate")
    assert handler is not None
    assert handler.target == (TemplateReadHandlers, "validate_template")  # type: ignore[attr-defined]


def test_unknown_command_returns_none():
    from orb.cli.registry import build_registry, lookup

    build_registry()
    assert lookup("nonexistent", "action") is None


def test_storage_validate_is_registered():
    from orb.cli.registry import _REGISTRY, build_registry

    build_registry()
    assert ("storage", "validate") in _REGISTRY


def test_storage_validate_handler_is_correct():
    from orb.cli.registry import _REGISTRY, build_registry
    from orb.interface.storage_command_handlers import StorageHandlers

    build_registry()
    assert _REGISTRY[("storage", "validate")].target == (  # type: ignore[attr-defined]
        StorageHandlers,
        "validate_storage_config",
    )
