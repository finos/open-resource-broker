"""A header-selected format renders the same data as the configured one.

Templates reach every format already merged through the configuration hierarchy
(global, provider type, provider instance, template), because the strategy for a
named format is built from the same container as the configured one. The CLI
``--scheduler`` override must also be in effect before the formatter is built.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.providers.aws.mocked.conftest import (
    _inject_moto_factory,
    _make_logger,
    _make_moto_aws_client,
)
from tests.providers.aws.mocked.test_cli_onmoto import _run_orb_cli
from tests.utilities.reset_singletons import reset_all_singletons

pytestmark = [pytest.mark.moto, pytest.mark.rest_api]


def _configure(config_dir: Path, scheduler: str) -> None:
    """Set the configured scheduler and one distinct default at every hierarchy level."""
    path = config_dir / "config.json"
    config = json.loads(path.read_text())
    config["scheduler"]["type"] = scheduler
    config["template"] = {"default_price_type": "spot"}  # global
    provider = config["provider"]
    provider["provider_defaults"] = {  # provider type
        "aws": {"template_defaults": {"allocation_strategy": "capacityOptimized"}}
    }
    provider["providers"][0]["template_defaults"]["allocation_strategy_on_demand"] = (
        "prioritized"  # provider instance
    )
    path.write_text(json.dumps(config))

    templates_path = config_dir / "aws_templates.json"
    templates = json.loads(templates_path.read_text())
    templates["templates"][0]["maxNumber"] = 7  # template row
    templates_path.write_text(json.dumps(templates))


def _build_app():
    from orb.api.dependencies import CurrentUser, get_current_user
    from orb.api.server import create_fastapi_app
    from orb.bootstrap.server_services import _register_orchestrators
    from orb.config.schemas.server_schema import ServerConfig
    from orb.infrastructure.di.container import get_container, reset_container

    reset_container()
    reset_all_singletons()
    container = get_container()
    _register_orchestrators(container)
    _inject_moto_factory(_make_moto_aws_client(), _make_logger(), None)
    app = create_fastapi_app(
        ServerConfig.model_validate({"enabled": True, "auth": {"enabled": False}})
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="viewer", role="viewer", claims={}
    )
    return app


async def _templates(app, headers: dict[str, str]) -> tuple[dict, str]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/templates/", headers=headers)
    assert response.status_code == 200, response.text
    return response.json(), response.headers["x-orb-scheduler"]


@pytest.mark.asyncio
async def test_header_selected_hostfactory_applies_the_same_hierarchy_as_configured_hostfactory(
    orb_config_dir,
):
    _configure(orb_config_dir, "hostfactory")
    configured_body, configured_name = await _templates(_build_app(), {})

    _configure(orb_config_dir, "default")
    selected_body, selected_name = await _templates(
        _build_app(), {"X-ORB-Scheduler": "hostfactory"}
    )

    assert (configured_name, selected_name) == ("hostfactory", "hostfactory")
    assert selected_body == configured_body
    rendered = json.dumps(selected_body)
    for value in ("capacityOptimized", "prioritized"):
        assert value in rendered, f"{value} missing from the header-selected body"
    assert any(t.get("maxNumber") == 7 for t in selected_body["templates"])


@pytest.mark.asyncio
async def test_naming_the_configured_format_is_the_configured_formatter(orb_config_dir):
    _configure(orb_config_dir, "hostfactory")
    app = _build_app()

    plain, _ = await _templates(app, {})
    named, name = await _templates(app, {"X-ORB-Scheduler": "hostfactory"})

    assert name == "hostfactory"
    assert named == plain


def test_cli_scheduler_override_is_in_effect_before_the_formatter_is_built(
    orb_config_dir, moto_aws
):
    from unittest.mock import patch

    from orb.bootstrap import Application
    from tests.providers.aws.mocked.test_cli_onmoto import _make_patched_initialize

    _configure(orb_config_dir, "default")
    patched = _make_patched_initialize(_make_moto_aws_client(), _make_logger())

    with patch.object(Application, "initialize", patched):
        overridden = _run_orb_cli(["templates", "list", "--scheduler", "hostfactory"])
    with patch.object(Application, "initialize", patched):
        configured = _run_orb_cli(["templates", "list"])

    assert all("templateId" in t for t in overridden["templates"])
    assert all("template_id" in t for t in configured["templates"])
