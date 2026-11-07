"""Template loads with a provider override must not share the cached template list."""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager
from orb.infrastructure.template.template_cache_service import create_template_cache_service

pytestmark = pytest.mark.unit


class _CountingScheduler:
    """Scheduler stub whose templates depend on the provider override."""

    def __init__(self) -> None:
        self.loads: list[Optional[str]] = []

    def get_template_paths(self) -> list[str]:
        return ["/fake/templates.json"]

    def load_templates_from_path(
        self, template_path: str, provider_override: Any = None
    ) -> list[dict[str, Any]]:
        self.loads.append(provider_override)
        template_id = f"tpl-{provider_override}" if provider_override else "tpl-default"
        return [{"template_id": template_id, "provider_api": "EC2Fleet", "max_instances": 1}]


def _manager() -> tuple[TemplateConfigurationManager, _CountingScheduler]:
    logger = MagicMock()
    config = MagicMock()
    config.get_provider_config.return_value = None
    scheduler = _CountingScheduler()
    manager = TemplateConfigurationManager(
        config_manager=config,
        scheduler_strategy=scheduler,  # type: ignore[arg-type]
        logger=logger,
        cache_service=create_template_cache_service("ttl", logger),
    )
    return manager, scheduler


def _ids(templates: list[Any]) -> list[str]:
    return [t.template_id for t in templates]


async def _load(manager: TemplateConfigurationManager, override: Optional[str] = None):
    return await manager.load_templates(provider_override=override)


class TestProviderOverrideBypassesCache:
    def test_override_load_does_not_poison_the_plain_list(self):
        manager, scheduler = _manager()

        async def _run():
            override = await _load(manager, "other-provider")
            plain = await _load(manager)
            return override, plain

        override, plain = asyncio.run(_run())

        assert _ids(override) == ["tpl-other-provider"]
        assert _ids(plain) == ["tpl-default"]
        assert scheduler.loads == ["other-provider", None]

    def test_override_load_after_plain_load_returns_override_rows(self):
        manager, scheduler = _manager()

        async def _run():
            plain = await _load(manager)
            override = await _load(manager, "other-provider")
            plain_again = await _load(manager)
            return plain, override, plain_again

        plain, override, plain_again = asyncio.run(_run())

        assert _ids(plain) == ["tpl-default"]
        assert _ids(override) == ["tpl-other-provider"]
        assert _ids(plain_again) == ["tpl-default"]
        # The third call is served from the cache filled by the first one.
        assert scheduler.loads == [None, "other-provider"]

    def test_force_refresh_with_override_leaves_cache_free_of_override_rows(self):
        manager, scheduler = _manager()

        async def _run():
            await _load(manager)
            await manager.load_templates(force_refresh=True, provider_override="other-provider")
            return await _load(manager)

        plain = asyncio.run(_run())

        assert _ids(plain) == ["tpl-default"]
        assert scheduler.loads == [None, "other-provider", None]
