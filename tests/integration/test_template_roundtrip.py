"""Integration test: template create → get round-trip through the real DI container."""

import os

import pytest

from orb.application.dto.queries import GetTemplateQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.template.commands import CreateTemplateCommand, UpdateTemplateCommand
from orb.domain.base.exceptions import EntityNotFoundError
from orb.infrastructure.di.container import get_container, reset_container


@pytest.fixture(autouse=True)
def isolated_container(tmp_path):
    """Reset the DI container and point ORB_CONFIG_DIR at tmp_path for each test."""
    reset_container()
    os.environ["ORB_CONFIG_DIR"] = str(tmp_path)
    yield
    reset_container()
    os.environ.pop("ORB_CONFIG_DIR", None)


@pytest.fixture
def command_bus():
    return get_container().get(CommandBusPort)


@pytest.fixture
def query_bus():
    return get_container().get(QueryBusPort)


@pytest.mark.asyncio
async def test_get_nonexistent_template_raises(query_bus):
    """GetTemplateQuery for a template that does not exist raises EntityNotFoundError."""
    with pytest.raises(EntityNotFoundError):
        await query_bus.execute(GetTemplateQuery(template_id="no-such-template"))


@pytest.mark.asyncio
async def test_create_then_get_roundtrip(command_bus, query_bus):
    """CreateTemplateCommand → disk → GetTemplateQuery returns correct DTO."""
    cmd = CreateTemplateCommand(
        template_id="tpl-roundtrip-001",
        name="Roundtrip Template",
        provider_api="EC2Fleet",
        image_id="ami-roundtrip01",
    )

    await command_bus.execute(cmd)

    assert cmd.created is True
    assert not cmd.validation_errors

    dto = await query_bus.execute(GetTemplateQuery(template_id="tpl-roundtrip-001"))

    assert dto.template_id == "tpl-roundtrip-001"
    assert dto.name == "Roundtrip Template"
    assert dto.provider_api == "EC2Fleet"
    assert dto.image_id == "ami-roundtrip01"


@pytest.mark.asyncio
async def test_create_update_get_roundtrip(command_bus, query_bus):
    """Create → update → get asserts updated fields are persisted."""
    create_cmd = CreateTemplateCommand(
        template_id="tpl-update-001",
        name="Original Name",
        provider_api="SpotFleet",
        image_id="ami-original01",
    )
    await command_bus.execute(create_cmd)
    assert create_cmd.created is True

    update_cmd = UpdateTemplateCommand(
        template_id="tpl-update-001",
        name="Updated Name",
        image_id="ami-updated01",
    )
    await command_bus.execute(update_cmd)
    assert update_cmd.updated is True

    dto = await query_bus.execute(GetTemplateQuery(template_id="tpl-update-001"))

    assert dto.template_id == "tpl-update-001"
    assert dto.name == "Updated Name"
    assert dto.image_id == "ami-updated01"
    assert dto.provider_api == "SpotFleet"
