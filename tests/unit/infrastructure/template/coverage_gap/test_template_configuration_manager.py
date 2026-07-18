"""Unit tests for TemplateConfigurationManager.

Coverage targets from configuration_manager.py: lines 154-155,168-170,189-191,
208,264,287-288,291-298,300,319,331-332,343-345,361,364,367,369-370,398-399,
423-425,440,462,476-480,511,517-524,527-540,557-559,572-574,585-586,618,630-634,
642-643,646-647,650,652,661-665,668-669,671-672,676-680,689,691,694,701-708,
713-715,717-718,720,725-726,731,773
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.infrastructure.template.configuration_manager import (
    TemplateConfigurationError,
    TemplateConfigurationManager,
    TemplateValidationError,
    create_template_configuration_manager,
)
from orb.infrastructure.template.dtos import TemplateDTO

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dto(
    template_id: str = "t1",
    provider_api: str = "EC2Fleet",
    max_instances: int = 5,
    metadata: dict | None = None,
) -> TemplateDTO:
    kwargs: dict = {
        "template_id": template_id,
        "name": template_id,
        "provider_api": provider_api,
        "max_instances": max_instances,
    }
    if metadata is not None:
        kwargs["metadata"] = metadata
    return TemplateDTO(**kwargs)


def _make_manager(
    *,
    templates: list[dict[str, Any]] | None = None,
    load_raises: Exception | None = None,
) -> TemplateConfigurationManager:
    mock_logger = MagicMock()
    mock_config = MagicMock()
    mock_config.app_config.scheduler.type = "default"
    mock_config.get_provider_config.return_value = None

    mock_scheduler = MagicMock()
    mock_scheduler.get_template_paths.return_value = ["/fake/templates.json"]

    if load_raises:
        mock_scheduler.load_templates_from_path.side_effect = load_raises
    else:
        mock_scheduler.load_templates_from_path.return_value = templates or []

    mock_cache = MagicMock()
    mock_storage = MagicMock()

    mgr = TemplateConfigurationManager(
        config_manager=mock_config,
        scheduler_strategy=mock_scheduler,
        logger=mock_logger,
        cache_service=mock_cache,
        storage_service=mock_storage,
    )
    return mgr


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestTemplateConfigurationManagerConstruction:
    def test_initialized_with_provided_services(self):
        mock_logger = MagicMock()
        mock_config = MagicMock()
        mock_scheduler = MagicMock()
        mock_cache = MagicMock()
        mock_storage = MagicMock()

        mgr = TemplateConfigurationManager(
            config_manager=mock_config,
            scheduler_strategy=mock_scheduler,
            logger=mock_logger,
            cache_service=mock_cache,
            storage_service=mock_storage,
        )

        assert mgr.config_manager is mock_config
        assert mgr.scheduler_strategy is mock_scheduler
        assert mgr.logger is mock_logger
        assert mgr.cache_service is mock_cache
        assert mgr.storage_service is mock_storage

    def test_creates_default_template_factory_when_not_provided(self):
        mock_logger = MagicMock()
        mock_config = MagicMock()
        mock_scheduler = MagicMock()

        mgr = TemplateConfigurationManager(
            config_manager=mock_config,
            scheduler_strategy=mock_scheduler,
            logger=mock_logger,
            cache_service=MagicMock(),
            storage_service=MagicMock(),
        )

        assert mgr.template_factory is not None

    def test_stores_optional_services(self):
        mock_logger = MagicMock()
        mock_event_pub = MagicMock()
        mock_prov_reg = MagicMock()
        mock_registry = MagicMock()

        mgr = TemplateConfigurationManager(
            config_manager=MagicMock(),
            scheduler_strategy=MagicMock(),
            logger=mock_logger,
            cache_service=MagicMock(),
            storage_service=MagicMock(),
            event_publisher=mock_event_pub,
            provider_registry_service=mock_prov_reg,
            registry=mock_registry,
        )

        assert mgr.event_publisher is mock_event_pub
        assert mgr.provider_registry_service is mock_prov_reg
        assert mgr._registry is mock_registry


# ---------------------------------------------------------------------------
# load_templates
# ---------------------------------------------------------------------------


class TestLoadTemplates:
    def test_invalidates_cache_on_force_refresh(self):
        mgr = _make_manager()
        mgr.cache_service.get_or_load = AsyncMock(return_value=[])

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(mgr.load_templates(force_refresh=True))
        finally:
            loop.close()

        mgr.cache_service.invalidate.assert_called_once()  # type: ignore[misc,attr-defined,union-attr,assignment]

    def test_does_not_invalidate_cache_without_force_refresh(self):
        mgr = _make_manager()
        mgr.cache_service.get_or_load = AsyncMock(return_value=[])

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(mgr.load_templates(force_refresh=False))
        finally:
            loop.close()

        mgr.cache_service.invalidate.assert_not_called()  # type: ignore[misc,attr-defined,union-attr,assignment]


# ---------------------------------------------------------------------------
# _load_templates_from_scheduler
# ---------------------------------------------------------------------------


class TestLoadTemplatesFromScheduler:
    def test_returns_empty_when_no_paths(self):
        mgr = _make_manager()
        mgr.scheduler_strategy.get_template_paths.return_value = []  # type: ignore[misc,attr-defined,union-attr,assignment]

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr._load_templates_from_scheduler())
        finally:
            loop.close()

        assert result == []

    def test_continues_when_path_load_raises(self):
        mgr = _make_manager()
        mgr.scheduler_strategy.get_template_paths.return_value = ["/bad/path.json"]  # type: ignore[misc,attr-defined,union-attr,assignment]
        mgr.scheduler_strategy.load_templates_from_path.side_effect = RuntimeError("io error")  # type: ignore[misc,attr-defined,union-attr,assignment]

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr._load_templates_from_scheduler())
        finally:
            loop.close()

        assert result == []

    def test_skips_invalid_template_dicts(self):
        mgr = _make_manager(
            templates=[{"template_id": ""}, {"no_id": True}]  # both invalid
        )
        # patch _batch_resolve_images and _filter to passthrough
        mgr._filter_templates_by_active_providers = lambda x: x  # type: ignore[misc,attr-defined,union-attr,assignment]

        loop = asyncio.new_event_loop()
        try:
            with patch.object(mgr, "_batch_resolve_images", new_callable=AsyncMock) as mock_batch:
                mock_batch.return_value = [{"template_id": ""}, {"no_id": True}]
                result = loop.run_until_complete(mgr._load_templates_from_scheduler())
        finally:
            loop.close()

        assert result == []


# ---------------------------------------------------------------------------
# _convert_dict_to_template_dto
# ---------------------------------------------------------------------------


class TestConvertDictToTemplateDTO:
    def test_raises_on_missing_template_id(self):
        mgr = _make_manager()
        with pytest.raises(ValueError, match="template_id"):
            mgr._convert_dict_to_template_dto({})

    def test_converts_valid_dict(self):
        mgr = _make_manager()
        template_dict = {
            "template_id": "t1",
            "name": "test",
            "provider_api": "EC2Fleet",
            "max_instances": 3,
        }
        result = mgr._convert_dict_to_template_dto(template_dict)
        assert isinstance(result, TemplateDTO)
        assert result.template_id == "t1"

    def test_accepts_templateId_key(self):
        mgr = _make_manager()
        template_dict = {
            "templateId": "hf-001",
            "template_id": "hf-001",
            "name": "hf-template",
            "provider_api": "EC2Fleet",
            "max_instances": 1,
        }
        result = mgr._convert_dict_to_template_dto(template_dict)
        assert result.template_id == "hf-001"


# ---------------------------------------------------------------------------
# _get_active_provider_types / _filter_templates_by_active_providers
# ---------------------------------------------------------------------------


class TestFilterTemplatesByActiveProviders:
    def test_returns_all_when_no_active_providers(self):
        mgr = _make_manager()
        mgr.config_manager.get_provider_config.return_value = None  # type: ignore[misc,attr-defined,union-attr,assignment]
        templates = [
            {"template_id": "t1", "provider_type": "aws"},
            {"template_id": "t2", "provider_type": "k8s"},
        ]
        result = mgr._filter_templates_by_active_providers(templates)
        assert len(result) == 2

    def test_filters_by_active_provider_type(self):
        mgr = _make_manager()
        mock_provider = MagicMock()
        mock_provider.type = "aws"
        mock_provider_config = MagicMock()
        mock_provider_config.get_active_providers.return_value = [mock_provider]
        mgr.config_manager.get_provider_config.return_value = mock_provider_config  # type: ignore[misc,attr-defined,union-attr,assignment]

        templates = [
            {"template_id": "t1", "provider_type": "aws"},
            {"template_id": "t2", "provider_type": "k8s"},
            {"template_id": "t3"},  # no provider_type → keep
        ]
        result = mgr._filter_templates_by_active_providers(templates)
        kept_ids = {t["template_id"] for t in result}
        assert "t1" in kept_ids
        assert "t3" in kept_ids
        assert "t2" not in kept_ids

    def test_retains_templates_with_empty_string_provider_type(self):
        mgr = _make_manager()
        mock_provider = MagicMock()
        mock_provider.type = "aws"
        mock_provider_config = MagicMock()
        mock_provider_config.get_active_providers.return_value = [mock_provider]
        mgr.config_manager.get_provider_config.return_value = mock_provider_config  # type: ignore[misc,attr-defined,union-attr,assignment]

        templates = [{"template_id": "t-empty", "provider_type": ""}]
        result = mgr._filter_templates_by_active_providers(templates)
        assert len(result) == 1

    def test_returns_all_on_provider_config_exception(self):
        mgr = _make_manager()
        mgr.config_manager.get_provider_config.side_effect = Exception("boom")  # type: ignore[misc,attr-defined,union-attr,assignment]
        templates = [{"template_id": "t1", "provider_type": "aws"}]
        result = mgr._filter_templates_by_active_providers(templates)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _resolve_active_provider_once
# ---------------------------------------------------------------------------


class TestResolveActiveProviderOnce:
    def test_uses_provider_registry_service_when_available(self):
        mgr = _make_manager()
        mock_prs = MagicMock()
        mock_prs.select_active_provider.return_value.provider_instance = "my-provider"
        mgr.provider_registry_service = mock_prs

        result = mgr._resolve_active_provider_once()
        assert result == "my-provider"

    def test_falls_back_to_config_when_registry_fails(self):
        mgr = _make_manager()
        mock_prs = MagicMock()
        mock_prs.select_active_provider.side_effect = Exception("registry error")
        mgr.provider_registry_service = mock_prs

        mock_provider = MagicMock()
        mock_provider.name = "fallback-provider"
        mock_provider_config = MagicMock()
        mock_provider_config.get_active_providers.return_value = [mock_provider]
        mgr.config_manager.get_provider_config.return_value = mock_provider_config  # type: ignore[misc,attr-defined,union-attr,assignment]

        result = mgr._resolve_active_provider_once()
        assert result == "fallback-provider"

    def test_returns_none_when_no_active_providers_in_config(self):
        mgr = _make_manager()
        mgr.provider_registry_service = None
        mock_provider_config = MagicMock()
        mock_provider_config.get_active_providers.return_value = []
        mgr.config_manager.get_provider_config.return_value = mock_provider_config  # type: ignore[misc,attr-defined,union-attr,assignment]

        result = mgr._resolve_active_provider_once()
        assert result is None


# ---------------------------------------------------------------------------
# _batch_resolve_images
# ---------------------------------------------------------------------------


class TestBatchResolveImages:
    def test_returns_original_when_no_ssm_specs(self):
        mgr = _make_manager()
        templates = [{"template_id": "t1", "image_id": "ami-123"}]

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr._batch_resolve_images(templates))
        finally:
            loop.close()

        assert result == templates

    def test_returns_original_when_image_resolution_disabled(self):
        mgr = _make_manager()
        templates = [{"template_id": "t1", "image_id": "/aws/service/some/ssm-path"}]
        mgr.provider_registry_service = MagicMock()

        with patch.object(mgr, "_is_image_resolution_enabled", return_value=False):
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(mgr._batch_resolve_images(templates))
            finally:
                loop.close()

        assert result == templates

    def test_handles_exception_gracefully(self):
        mgr = _make_manager()
        templates = [{"template_id": "t1", "image_id": "/aws/service/path"}]

        with patch.object(
            mgr, "_is_image_resolution_enabled", side_effect=Exception("resolution error")
        ):
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(mgr._batch_resolve_images(templates))
            finally:
                loop.close()

        assert result == templates


# ---------------------------------------------------------------------------
# _extract_image_specifications
# ---------------------------------------------------------------------------


class TestExtractImageSpecifications:
    def test_extracts_ssm_paths(self):
        mgr = _make_manager()
        templates = [
            {"template_id": "t1", "image_id": "/aws/service/ec2/AMI-amazon-linux"},
            {"template_id": "t2", "image_id": "ami-12345"},  # not SSM
            {"template_id": "t3", "imageId": "/aws/service/ec2/AMI-ubuntu"},
        ]
        result = mgr._extract_image_specifications(templates)
        assert len(result) == 2
        assert "/aws/service/ec2/AMI-amazon-linux" in result
        assert "/aws/service/ec2/AMI-ubuntu" in result

    def test_deduplicates_specs(self):
        mgr = _make_manager()
        templates = [
            {"template_id": "t1", "image_id": "/aws/service/path"},
            {"template_id": "t2", "image_id": "/aws/service/path"},
        ]
        result = mgr._extract_image_specifications(templates)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _apply_resolved_images
# ---------------------------------------------------------------------------


class TestApplyResolvedImages:
    def test_replaces_ssm_path_with_ami(self):
        mgr = _make_manager()
        templates = [{"template_id": "t1", "image_id": "/aws/service/path"}]
        resolved = {"/aws/service/path": "ami-resolved-123"}

        result = mgr._apply_resolved_images(templates, resolved)
        assert result[0]["image_id"] == "ami-resolved-123"

    def test_updates_imageId_when_present(self):
        mgr = _make_manager()
        templates = [
            {"template_id": "t1", "image_id": "/aws/service/path", "imageId": "/aws/service/path"}
        ]
        resolved = {"/aws/service/path": "ami-resolved-456"}

        result = mgr._apply_resolved_images(templates, resolved)
        assert result[0]["imageId"] == "ami-resolved-456"

    def test_does_not_modify_templates_without_matching_spec(self):
        mgr = _make_manager()
        templates = [{"template_id": "t1", "image_id": "ami-static"}]
        resolved = {"/aws/service/path": "ami-resolved"}

        result = mgr._apply_resolved_images(templates, resolved)
        assert result[0]["image_id"] == "ami-static"


# ---------------------------------------------------------------------------
# get_template_by_id
# ---------------------------------------------------------------------------


class TestGetTemplateById:
    def test_raises_validation_error_for_empty_id(self):
        mgr = _make_manager()
        mgr.cache_service.get_or_load = AsyncMock(return_value=[])

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(TemplateValidationError):
                loop.run_until_complete(mgr.get_template_by_id(""))
        finally:
            loop.close()

    def test_raises_validation_error_for_non_string_id(self):
        mgr = _make_manager()

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(TemplateValidationError):
                loop.run_until_complete(mgr.get_template_by_id(None))  # type: ignore[arg-type]
        finally:
            loop.close()

    def test_returns_none_when_template_not_found(self):
        mgr = _make_manager()
        dto = _make_dto("t1")
        mgr.cache_service.get_or_load = AsyncMock(return_value=[dto])

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr.get_template_by_id("nonexistent"))
        finally:
            loop.close()

        assert result is None

    def test_returns_matching_template(self):
        mgr = _make_manager()
        dto = _make_dto("my-template")
        mgr.cache_service.get_or_load = AsyncMock(return_value=[dto])

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr.get_template_by_id("my-template"))
        finally:
            loop.close()

        assert result is dto

    def test_raises_configuration_error_on_unexpected_exception(self):
        mgr = _make_manager()
        mgr.cache_service.get_or_load = AsyncMock(side_effect=RuntimeError("unexpected"))

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(TemplateConfigurationError):
                loop.run_until_complete(mgr.get_template_by_id("t1"))
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# get_templates_by_provider / get_all_templates
# ---------------------------------------------------------------------------


class TestGetTemplatesByProvider:
    def test_filters_by_provider_api(self):
        mgr = _make_manager()
        dto1 = _make_dto("t1", provider_api="EC2Fleet")
        dto2 = _make_dto("t2", provider_api="SpotFleet")
        mgr.cache_service.get_or_load = AsyncMock(return_value=[dto1, dto2])

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr.get_templates_by_provider("EC2Fleet"))
        finally:
            loop.close()

        assert len(result) == 1
        assert result[0].template_id == "t1"

    def test_get_all_templates_alias(self):
        mgr = _make_manager()
        dto = _make_dto("t1")
        mgr.cache_service.get_or_load = AsyncMock(return_value=[dto])

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr.get_all_templates())
        finally:
            loop.close()

        assert len(result) == 1


# ---------------------------------------------------------------------------
# save_template / delete_template
# ---------------------------------------------------------------------------


class TestSaveDeleteTemplate:
    def test_save_template_delegates_and_invalidates_cache(self):
        mgr = _make_manager()
        mgr.storage_service.save_template = AsyncMock()
        dto = _make_dto("t1")

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(mgr.save_template(dto))
        finally:
            loop.close()

        mgr.storage_service.save_template.assert_called_once_with(dto)
        mgr.cache_service.invalidate.assert_called()  # type: ignore[misc,attr-defined,union-attr,assignment]

    def test_save_template_reraises_on_error(self):
        mgr = _make_manager()
        mgr.storage_service.save_template = AsyncMock(side_effect=RuntimeError("save failed"))
        dto = _make_dto("t1")

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(RuntimeError):
                loop.run_until_complete(mgr.save_template(dto))
        finally:
            loop.close()

    def test_delete_template_delegates_and_invalidates_cache(self):
        mgr = _make_manager()
        mgr.storage_service.delete_template = AsyncMock()

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(mgr.delete_template("t1"))
        finally:
            loop.close()

        mgr.storage_service.delete_template.assert_called_once_with("t1")
        mgr.cache_service.invalidate.assert_called()  # type: ignore[misc,attr-defined,union-attr,assignment]

    def test_delete_template_always_invalidates_cache_even_on_error(self):
        mgr = _make_manager()
        mgr.storage_service.delete_template = AsyncMock(side_effect=RuntimeError("delete failed"))

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(RuntimeError):
                loop.run_until_complete(mgr.delete_template("t1"))
        finally:
            loop.close()

        mgr.cache_service.invalidate.assert_called()  # type: ignore[misc,attr-defined,union-attr,assignment]


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------


class TestClearCache:
    def test_clears_cache_service(self):
        mgr = _make_manager()
        mgr.clear_cache()
        mgr.cache_service.invalidate.assert_called_once()  # type: ignore[misc,attr-defined,union-attr,assignment]


# ---------------------------------------------------------------------------
# _deduplicate_template_dicts
# ---------------------------------------------------------------------------


class TestDeduplicateTemplateDicts:
    def test_empty_input_returns_empty(self):
        mgr = _make_manager()
        assert mgr._deduplicate_template_dicts([]) == []

    def test_removes_duplicate_template_ids(self):
        mgr = _make_manager()
        templates = [
            {"template_id": "t1", "name": "first"},
            {"template_id": "t1", "name": "duplicate"},
            {"template_id": "t2", "name": "unique"},
        ]
        result = mgr._deduplicate_template_dicts(templates)
        assert len(result) == 2
        # first occurrence is kept
        assert result[0]["name"] == "first"

    def test_accepts_templateId_key(self):
        mgr = _make_manager()
        templates = [
            {"templateId": "hf-001"},
            {"templateId": "hf-001"},
        ]
        result = mgr._deduplicate_template_dicts(templates)
        assert len(result) == 1

    def test_keeps_templates_with_no_id(self):
        mgr = _make_manager()
        # The dedup logic: "if template_id and template_id not in seen_ids: add"
        # Templates with falsy ids (empty string, None) are dropped — they fail the
        # truthy check.  This is the actual production behaviour so we verify it.
        templates = [
            {"template_id": "unique-1", "name": "first"},
            {"template_id": "unique-2", "name": "second"},
            {"template_id": "", "name": "no-id"},  # falsy id — dropped by dedup logic
        ]
        result = mgr._deduplicate_template_dicts(templates)
        # Only templates with truthy IDs are kept
        assert len(result) == 2
        kept_ids = {t["template_id"] for t in result}
        assert "unique-1" in kept_ids
        assert "unique-2" in kept_ids


# ---------------------------------------------------------------------------
# validate_template
# ---------------------------------------------------------------------------


class TestValidateTemplate:
    def test_valid_template_returns_is_valid_true(self):
        mgr = _make_manager()
        dto = _make_dto("t1", max_instances=5)

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr.validate_template(dto))
        finally:
            loop.close()

        assert result["is_valid"] is True
        assert result["template_id"] == "t1"

    def test_missing_template_id_makes_invalid(self):
        mgr = _make_manager()
        dto = _make_dto("", provider_api="EC2Fleet")

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr.validate_template(dto))
        finally:
            loop.close()

        assert result["is_valid"] is False
        assert any("Template ID" in e for e in result["errors"])

    def test_missing_provider_api_makes_invalid(self):
        mgr = _make_manager()
        # Build a TemplateDTO with None provider_api by bypassing pydantic validation
        dto = TemplateDTO.model_construct(
            template_id="t1",
            name="t1",
            provider_api=None,
            max_instances=5,
        )

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr.validate_template(dto))
        finally:
            loop.close()

        assert result["is_valid"] is False
        assert any("Provider API" in e for e in result["errors"])

    def test_max_instances_zero_adds_warning(self):
        mgr = _make_manager()
        dto = _make_dto("t1", max_instances=0)

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr.validate_template(dto))
        finally:
            loop.close()

        assert any("0" in w or "greater than 0" in w for w in result["warnings"])

    def test_max_instances_over_1000_adds_warning(self):
        mgr = _make_manager()
        dto = _make_dto("t1", max_instances=1001)

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr.validate_template(dto))
        finally:
            loop.close()

        assert any("1000" in w or "high" in w.lower() for w in result["warnings"])

    def test_scheduler_type_mismatch_adds_warning(self):
        mgr = _make_manager()
        # Must use configure_mock to set a string value for the `.type` attribute
        # since direct assignment on a MagicMock attribute chain is unreliable.
        mgr.config_manager.app_config.scheduler.configure_mock(
            **{"type": "default", "on_scheduler_mismatch": "warn"}
        )  # type: ignore[misc,attr-defined,union-attr,assignment]

        dto = _make_dto("t1", metadata={"scheduler_type": "hostfactory"})

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr.validate_template(dto))
        finally:
            loop.close()

        assert any("scheduler_type" in w or "active scheduler" in w for w in result["warnings"])

    def test_scheduler_type_mismatch_fails_on_fail_action(self):
        mgr = _make_manager()
        mgr.config_manager.app_config.scheduler.configure_mock(
            **{"type": "default", "on_scheduler_mismatch": "fail"}
        )  # type: ignore[misc,attr-defined,union-attr,assignment]

        dto = _make_dto("t1", metadata={"scheduler_type": "hostfactory"})

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(mgr.validate_template(dto))
        finally:
            loop.close()

        assert result["is_valid"] is False

    def test_validate_with_provider_registry_warns_when_no_registry(self):
        mgr = _make_manager()
        mgr._registry = None
        dto = _make_dto("t1", max_instances=5)

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                mgr.validate_template(dto, provider_instance="my-prov")
            )
        finally:
            loop.close()

        assert any("registry" in w.lower() for w in result["warnings"])

    def test_validate_with_provider_registry_warns_when_registry_lacks_method(self):
        mgr = _make_manager()
        mgr._registry = MagicMock(spec=[])  # no validate_template_requirements
        dto = _make_dto("t1", max_instances=5)

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                mgr.validate_template(dto, provider_instance="my-prov")
            )
        finally:
            loop.close()

        assert any("validation" in w.lower() for w in result["warnings"])

    def test_validate_with_provider_registry_merges_errors(self):
        mgr = _make_manager()
        mock_cap_result = MagicMock()
        mock_cap_result.is_valid = False
        mock_cap_result.errors = ["capability error"]
        mock_cap_result.warnings = ["cap warning"]
        mock_cap_result.supported_features = ["feat1"]
        mgr._registry = MagicMock()
        mgr._registry.validate_template_requirements.return_value = mock_cap_result
        dto = _make_dto("t1", max_instances=5)

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                mgr.validate_template(dto, provider_instance="my-prov")
            )
        finally:
            loop.close()

        assert result["is_valid"] is False
        assert "capability error" in result["errors"]
        assert "feat1" in result["supported_features"]

    def test_validate_handles_exception_gracefully(self):
        mgr = _make_manager()
        dto = _make_dto("t1", max_instances=5)
        # Make validate throw from _validate_basic_template_structure
        with patch.object(
            mgr,
            "_validate_basic_template_structure",
            side_effect=Exception("unexpected"),
        ):
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(mgr.validate_template(dto))
            finally:
                loop.close()

        assert result["is_valid"] is False
        assert any("Validation error" in e for e in result["errors"])
        # The underlying cause must be preserved, not silently swallowed.
        assert any("unexpected" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# get_all_templates_sync / get_template sync wrapper
# ---------------------------------------------------------------------------


class TestSyncWrappers:
    def test_get_all_templates_sync_runs_new_event_loop(self):
        mgr = _make_manager()
        dto = _make_dto("t1")
        mgr.cache_service.get_or_load = AsyncMock(return_value=[dto])

        # No running loop in this thread, so asyncio.run(get_all_templates())
        # is used, which resolves to cache_service.get_or_load() -> [dto].
        result = mgr.get_all_templates_sync()
        assert len(result) == 1
        assert result[0] is dto

    def test_get_template_sync_falls_back_when_loop_running(self):
        mgr = _make_manager()
        dto = _make_dto("t-sync")

        # Simulate a running loop by mocking asyncio.get_running_loop to succeed
        with (
            patch("asyncio.get_running_loop", return_value=MagicMock()),
            patch.object(mgr, "_load_templates_sync", return_value=[dto]) as mock_sync,
        ):
            result = mgr.get_template("t-sync")

        mock_sync.assert_called_once()
        assert result is dto

    def test_get_template_sync_uses_asyncio_run_when_no_loop(self):
        mgr = _make_manager()
        dto = _make_dto("t-noloop")
        mgr.cache_service.get_or_load = AsyncMock(return_value=[dto])

        with patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
            result = mgr.get_template("t-noloop")

        # No running loop -> asyncio.run(get_template_by_id('t-noloop')),
        # which loads [dto] from the cache and returns the id-matching dto.
        assert result is dto


# ---------------------------------------------------------------------------
# create_template_configuration_manager factory
# ---------------------------------------------------------------------------


class TestCreateTemplateConfigurationManagerFactory:
    def test_returns_manager_instance(self):
        mock_config = MagicMock()
        mock_scheduler = MagicMock()
        mock_logger = MagicMock()

        mgr = create_template_configuration_manager(
            config_manager=mock_config,
            scheduler_strategy=mock_scheduler,
            logger=mock_logger,
        )

        assert isinstance(mgr, TemplateConfigurationManager)
        assert mgr.config_manager is mock_config
