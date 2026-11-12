"""POST /admin/init writes a provider configuration that loads, from the same builder as the CLI."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from tests.providers.aws.mocked.test_rest_api_onmoto import fastapi_app  # noqa: F401

pytestmark = [pytest.mark.moto, pytest.mark.rest_api]

_GUARD = "orb.api.routers.admin.check_destructive_admin_allowed"


@pytest.fixture
def init_dirs(tmp_path, monkeypatch):
    """Point the platform directories at an empty location for the init call."""
    root = tmp_path / "init-root"
    monkeypatch.setenv("ORB_CONFIG_DIR", str(root / "config"))
    monkeypatch.setenv("ORB_WORK_DIR", str(root / "work"))
    monkeypatch.setenv("ORB_LOG_DIR", str(root / "logs"))
    monkeypatch.setenv("ORB_SCRIPTS_DIR", str(root / "scripts"))
    return root


async def _post_init(app, body: dict):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    with patch(_GUARD):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v1/admin/init", json={"confirm": "INIT", "generate_templates": False, **body}
            )


@pytest.mark.asyncio
async def test_init_writes_a_provider_configuration_that_loads(fastapi_app, init_dirs):  # noqa: F811
    response = await _post_init(
        fastapi_app, {"provider_type": "aws", "options": {"aws_region": "eu-west-2"}}
    )

    assert response.status_code == 200, response.text
    config_file = init_dirs / "config" / "config.json"
    assert response.json()["created_files"] == [str(config_file)]

    from orb.config.loader import ConfigurationLoader

    loaded = ConfigurationLoader.load(str(config_file))
    providers = loaded["provider"]["providers"]
    assert len(providers) == 1
    assert providers[0]["type"] == "aws"
    assert providers[0]["config"]["region"] == "eu-west-2"
    assert loaded["provider"]["default_provider_instance"] == providers[0]["name"]


@pytest.mark.asyncio
async def test_init_with_rejected_credentials_writes_nothing(fastapi_app, init_dirs):  # noqa: F811
    with patch(
        "orb.interface.init_command_handler._test_provider_credentials",
        return_value=(False, "InvalidClientTokenId: secret-detail"),
    ):
        response = await _post_init(
            fastapi_app, {"provider_type": "aws", "options": {"aws_region": "eu-west-2"}}
        )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "CREDENTIALS_REJECTED"
    assert "secret-detail" not in response.text
    assert not init_dirs.exists()


@pytest.mark.asyncio
async def test_init_with_an_unregistered_provider_type_is_a_client_error(fastapi_app, init_dirs):  # noqa: F811
    response = await _post_init(fastapi_app, {"provider_type": "no-such-provider"})

    assert response.status_code == 400, response.text
    assert not init_dirs.exists()


@pytest.mark.asyncio
async def test_rest_and_cli_build_the_same_configuration(fastapi_app, init_dirs, tmp_path):  # noqa: F811
    from orb.infrastructure.di.container import get_container
    from orb.interface.init_command_handler import handle_init

    response = await _post_init(
        fastapi_app, {"provider_type": "aws", "options": {"aws_region": "eu-west-2"}}
    )
    assert response.status_code == 200, response.text

    cli_dir = tmp_path / "cli-root" / "config"
    args = Namespace(
        _container=get_container(),
        config_dir=str(cli_dir),
        force=False,
        non_interactive=True,
        scripts_dir=None,
        provider_type="aws",
        scheduler=None,
        aws_region="eu-west-2",
        aws_profile=None,
        subnet_ids=None,
        security_group_ids=None,
        fleet_role=None,
    )
    assert await handle_init(args) == 0

    rest = json.loads(Path(init_dirs / "config" / "config.json").read_text())
    cli = json.loads((cli_dir / "config.json").read_text())
    assert rest["provider"] == cli["provider"]
    assert rest["scheduler"] == cli["scheduler"]
