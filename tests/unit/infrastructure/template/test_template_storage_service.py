"""Tests for TemplateStorageService round-trip correctness."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.template.dtos import TemplateDTO
from orb.infrastructure.template.services.template_storage_service import TemplateStorageService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> MagicMock:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.error = MagicMock()
    logger.warning = MagicMock()
    return logger


def _make_dto(template_id: str = "tpl-001") -> TemplateDTO:
    return TemplateDTO(
        template_id=template_id,
        name=template_id,
        max_instances=3,
        machine_types={"t3.medium": 1},
        subnet_ids=["subnet-aaa"],
        security_group_ids=["sg-111"],
        price_type="ondemand",
        provider_api="EC2Fleet",
        provider_type="aws",
    )


def _make_service(strategy, tmp_path: Path) -> tuple[TemplateStorageService, Path]:
    logger = _make_logger()
    svc = TemplateStorageService(scheduler_strategy=strategy, logger=logger)
    # Patch get_template_paths to return a file inside tmp_path
    target = tmp_path / "templates.json"
    strategy.get_template_paths = MagicMock(return_value=[str(target)])
    return svc, target


# ---------------------------------------------------------------------------
# test_save_writes_scheduler_native_format
# ---------------------------------------------------------------------------


class TestSaveWritesSchedulerNativeFormat:
    """Written JSON must use the scheduler's native field names."""

    @pytest.mark.asyncio
    async def test_default_scheduler_writes_snake_case(self, tmp_path):
        strategy = DefaultSchedulerStrategy()
        svc, target = _make_service(strategy, tmp_path)

        await svc.save_template(_make_dto("tpl-default"))

        raw = json.loads(target.read_text())
        assert raw.get("scheduler_type") == "default"
        templates = raw.get("templates", [])
        assert len(templates) == 1
        # Default strategy keeps snake_case
        assert "template_id" in templates[0]
        assert templates[0]["template_id"] == "tpl-default"

    @pytest.mark.asyncio
    async def test_hostfactory_scheduler_writes_camel_case(self, tmp_path):
        strategy = HostFactorySchedulerStrategy()
        svc, target = _make_service(strategy, tmp_path)

        await svc.save_template(_make_dto("tpl-hf"))

        raw = json.loads(target.read_text())
        assert raw.get("scheduler_type") == "hostfactory"
        templates = raw.get("templates", [])
        assert len(templates) == 1
        # HF strategy converts to camelCase
        assert "templateId" in templates[0], (
            f"Expected camelCase 'templateId' key, got keys: {list(templates[0].keys())}"
        )
        assert templates[0]["templateId"] == "tpl-hf"
        assert "template_id" not in templates[0]


# ---------------------------------------------------------------------------
# test_save_roundtrip
# ---------------------------------------------------------------------------


class TestSaveRoundtrip:
    """After save, _load_templates_from_file returns the raw on-disk dicts."""

    @pytest.mark.asyncio
    async def test_default_roundtrip_preserves_template_id(self, tmp_path):
        strategy = DefaultSchedulerStrategy()
        svc, target = _make_service(strategy, tmp_path)
        dto = _make_dto("roundtrip-default")

        await svc.save_template(dto)

        loaded = await svc._load_templates_from_file(target)
        assert len(loaded) == 1
        assert loaded[0].get("template_id") == "roundtrip-default"

    @pytest.mark.asyncio
    async def test_hostfactory_roundtrip_preserves_template_id(self, tmp_path):
        strategy = HostFactorySchedulerStrategy()
        svc, target = _make_service(strategy, tmp_path)
        dto = _make_dto("roundtrip-hf")

        await svc.save_template(dto)

        loaded = await svc._load_templates_from_file(target)
        assert len(loaded) == 1
        # On-disk is camelCase; raw load returns it as-is
        assert loaded[0].get("templateId") == "roundtrip-hf"


# ---------------------------------------------------------------------------
# test_save_updates_existing
# ---------------------------------------------------------------------------


class TestSaveUpdatesExisting:
    """Saving a template with the same ID replaces it, not appends."""

    @pytest.mark.asyncio
    async def test_default_update_replaces_not_appends(self, tmp_path):
        strategy = DefaultSchedulerStrategy()
        svc, target = _make_service(strategy, tmp_path)
        dto = _make_dto("tpl-update")

        await svc.save_template(dto)
        # Save again with different max_instances
        dto2 = TemplateDTO(
            template_id="tpl-update",
            name="tpl-update",
            max_instances=10,
            machine_types={"m5.large": 1},
            subnet_ids=["subnet-bbb"],
            security_group_ids=["sg-222"],
            price_type="spot",
            provider_api="EC2Fleet",
            provider_type="aws",
        )
        await svc.save_template(dto2)

        raw = json.loads(target.read_text())
        templates = raw.get("templates", [])
        assert len(templates) == 1, f"Expected 1 template after update, got {len(templates)}"
        assert templates[0].get("max_instances") == 10

    @pytest.mark.asyncio
    async def test_hostfactory_update_replaces_not_appends(self, tmp_path):
        strategy = HostFactorySchedulerStrategy()
        svc, target = _make_service(strategy, tmp_path)

        # Pre-populate file with a native HF template
        existing = {
            "scheduler_type": "hostfactory",
            "templates": [
                {
                    "templateId": "tpl-hf-update",
                    "maxNumber": 2,
                    "vmType": "t3.small",
                    "subnetIds": ["subnet-old"],
                    "securityGroupIds": ["sg-old"],
                    "priceType": "ondemand",
                    "providerApi": "EC2Fleet",
                    "providerType": "aws",
                }
            ],
        }
        target.write_text(json.dumps(existing))

        dto = TemplateDTO(
            template_id="tpl-hf-update",
            name="tpl-hf-update",
            max_instances=7,
            machine_types={"m5.xlarge": 1},
            subnet_ids=["subnet-new"],
            security_group_ids=["sg-new"],
            price_type="spot",
            provider_api="EC2Fleet",
            provider_type="aws",
        )
        await svc.save_template(dto)

        raw = json.loads(target.read_text())
        templates = raw.get("templates", [])
        assert len(templates) == 1, f"Expected 1 template after update, got {len(templates)}"
        assert templates[0].get("templateId") == "tpl-hf-update"
        assert templates[0].get("maxNumber") == 7


# ---------------------------------------------------------------------------
# test_delete_removes_by_native_key
# ---------------------------------------------------------------------------


class TestDeleteRemovesByNativeKey:
    """Delete works when the file contains native-format (camelCase) keys."""

    @pytest.mark.asyncio
    async def test_delete_from_camelcase_file(self, tmp_path):
        strategy = HostFactorySchedulerStrategy()
        svc, target = _make_service(strategy, tmp_path)

        existing = {
            "scheduler_type": "hostfactory",
            "templates": [
                {
                    "templateId": "tpl-to-delete",
                    "maxNumber": 1,
                    "vmType": "t3.micro",
                    "subnetIds": ["subnet-x"],
                    "securityGroupIds": ["sg-x"],
                    "priceType": "ondemand",
                    "providerApi": "EC2Fleet",
                    "providerType": "aws",
                },
                {
                    "templateId": "tpl-to-keep",
                    "maxNumber": 2,
                    "vmType": "t3.small",
                    "subnetIds": ["subnet-y"],
                    "securityGroupIds": ["sg-y"],
                    "priceType": "ondemand",
                    "providerApi": "EC2Fleet",
                    "providerType": "aws",
                },
            ],
        }
        target.write_text(json.dumps(existing))

        await svc.delete_template("tpl-to-delete", source_file=target)

        raw = json.loads(target.read_text())
        templates = raw.get("templates", [])
        assert len(templates) == 1
        assert templates[0].get("templateId") == "tpl-to-keep"

    @pytest.mark.asyncio
    async def test_delete_from_snake_case_file(self, tmp_path):
        strategy = DefaultSchedulerStrategy()
        svc, target = _make_service(strategy, tmp_path)

        existing = {
            "scheduler_type": "default",
            "templates": [
                {
                    "template_id": "tpl-snake-delete",
                    "max_instances": 1,
                    "machine_types": {"t3.micro": 1},
                    "subnet_ids": ["subnet-x"],
                    "security_group_ids": ["sg-x"],
                    "price_type": "ondemand",
                    "provider_api": "EC2Fleet",
                    "provider_type": "aws",
                },
                {
                    "template_id": "tpl-snake-keep",
                    "max_instances": 2,
                    "machine_types": {"t3.small": 1},
                    "subnet_ids": ["subnet-y"],
                    "security_group_ids": ["sg-y"],
                    "price_type": "ondemand",
                    "provider_api": "EC2Fleet",
                    "provider_type": "aws",
                },
            ],
        }
        target.write_text(json.dumps(existing))

        await svc.delete_template("tpl-snake-delete", source_file=target)

        raw = json.loads(target.read_text())
        templates = raw.get("templates", [])
        assert len(templates) == 1
        assert templates[0].get("template_id") == "tpl-snake-keep"
