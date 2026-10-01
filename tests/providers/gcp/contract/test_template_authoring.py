"""GCP templates authored through the existing generic command contract."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.commands.template_handlers import CreateTemplateHandler, UpdateTemplateHandler
from orb.application.template.commands import CreateTemplateCommand, UpdateTemplateCommand
from orb.infrastructure.adapters.template_configuration_adapter import TemplateConfigurationAdapter


@pytest.mark.asyncio
async def test_generic_create_and_update_preserve_standard_gcp_fields() -> None:
    manager = MagicMock()
    manager._registry = None
    manager.get_template_by_id = AsyncMock(return_value=None)
    manager.save_template = AsyncMock()
    port = TemplateConfigurationAdapter(manager, MagicMock())
    create_handler = CreateTemplateHandler(port, MagicMock(), MagicMock(), MagicMock())

    create = CreateTemplateCommand(
        template_id="gcp-standard",
        provider_api="SingleVM",
        image_id="projects/debian-cloud/global/images/debian-12-v1",
        configuration={
            "provider_type": "gcp",
            "machine_types": {"e2-micro": 1},
            "network_zones": ["us-central1-a"],
        },
    )
    await create_handler.handle(create)

    assert create.created
    saved = manager.save_template.call_args.args[0]
    assert saved.image_id == create.image_id
    assert saved.machine_types == {"e2-micro": 1}
    assert saved.network_zones == ["us-central1-a"]
    assert saved.provider_config is None

    manager.get_template_by_id.return_value = saved
    update_handler = UpdateTemplateHandler(port, MagicMock(), MagicMock(), MagicMock())
    update = UpdateTemplateCommand(
        template_id="gcp-standard",
        configuration={"machine_types": {"e2-small": 1}},
    )
    await update_handler.handle(update)

    assert update.updated
    updated = manager.save_template.call_args.args[0]
    assert updated.machine_types == {"e2-small": 1}
    assert updated.network_zones == ["us-central1-a"]
