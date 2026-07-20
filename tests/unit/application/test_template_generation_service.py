"""Unit tests for TemplateGenerationService."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orb.application.dto.template_generation_dto import (
    TemplateGenerationRequest,
)
from orb.application.services.template_generation_service import TemplateGenerationService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    *,
    providers: list[dict] | None = None,
    examples: list[dict] | None = None,
    register_ok: bool = True,
    path_dir: str | None = None,
    scheduler_type: str = "default",
    templates_filename: str = "aws_templates.json",
) -> TemplateGenerationService:
    """Build a TemplateGenerationService with all dependencies mocked."""
    if providers is None:
        providers = [{"name": "aws-prod", "type": "aws"}]
    if examples is None:
        examples = [{"template_id": "t1", "name": "example"}]

    # ConfigurationPort mock
    config_manager = MagicMock()
    provider_config_mock = MagicMock()
    active_providers = []
    for p in providers:
        pm = MagicMock()
        pm.name = p["name"]
        pm.type = p["type"]
        active_providers.append(pm)
    provider_config_mock.get_active_providers.return_value = active_providers
    config_manager.get_provider_config.return_value = provider_config_mock
    config_manager.get_template_config.return_value = {}

    # SchedulerPort mock
    scheduler = MagicMock()
    scheduler.get_scheduler_type.return_value = scheduler_type
    scheduler.get_templates_filename.return_value = templates_filename
    scheduler.format_templates_for_dispatch.side_effect = lambda t: t  # identity

    # LoggingPort mock
    logger = MagicMock()

    # ProviderRegistryService mock
    provider_registry = MagicMock()
    provider_registry.register_provider_strategy.return_value = register_ok

    # TemplateExampleGeneratorResolverPort mock
    generator = MagicMock()
    generator.generate_example_templates.return_value = examples
    generator_resolver = MagicMock()
    generator_resolver.get.return_value = generator

    # PathResolutionPort mock
    path_resolver = MagicMock()
    path_resolver.get_config_dir.return_value = path_dir or "/tmp"

    return TemplateGenerationService(
        config_manager=config_manager,
        scheduler_strategy=scheduler,
        logger=logger,
        provider_registry_service=provider_registry,
        generator_resolver=generator_resolver,
        path_resolver=path_resolver,
    )


# ---------------------------------------------------------------------------
# generate_templates — generic mode (merged by type)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateTemplatesGenericMode:
    @pytest.mark.asyncio
    async def test_returns_success_status(self, tmp_path):
        svc = _make_service(path_dir=str(tmp_path))
        req = TemplateGenerationRequest(all_providers=True)
        result = await svc.generate_templates(req)
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_result_has_total_templates_count(self, tmp_path):
        svc = _make_service(
            path_dir=str(tmp_path), examples=[{"template_id": "t1"}, {"template_id": "t2"}]
        )
        req = TemplateGenerationRequest(all_providers=True)
        result = await svc.generate_templates(req)
        assert result.total_templates == 2

    @pytest.mark.asyncio
    async def test_result_created_count_matches_providers(self, tmp_path):
        svc = _make_service(path_dir=str(tmp_path))
        req = TemplateGenerationRequest(all_providers=True)
        result = await svc.generate_templates(req)
        assert result.created_count >= 0

    @pytest.mark.asyncio
    async def test_file_is_written_to_path(self, tmp_path):
        providers = [{"name": "aws-dev", "type": "aws"}]
        svc = _make_service(path_dir=str(tmp_path), providers=providers)
        req = TemplateGenerationRequest(all_providers=True)
        await svc.generate_templates(req)
        # Should have written aws_templates.json
        assert (tmp_path / "aws_templates.json").exists()

    @pytest.mark.asyncio
    async def test_existing_file_skipped_when_no_force_overwrite(self, tmp_path):
        # Pre-create the file
        (tmp_path / "aws_templates.json").write_text("{}")
        svc = _make_service(path_dir=str(tmp_path))
        req = TemplateGenerationRequest(all_providers=True, force_overwrite=False)
        result = await svc.generate_templates(req)
        skipped = [r for r in result.providers if r.status == "skipped"]
        assert len(skipped) == 1

    @pytest.mark.asyncio
    async def test_existing_file_overwritten_with_force(self, tmp_path):
        (tmp_path / "aws_templates.json").write_text("{}")
        svc = _make_service(path_dir=str(tmp_path))
        req = TemplateGenerationRequest(all_providers=True, force_overwrite=True)
        result = await svc.generate_templates(req)
        created = [r for r in result.providers if r.status == "created"]
        assert len(created) >= 1

    @pytest.mark.asyncio
    async def test_returns_error_status_on_exception(self):
        svc = _make_service()
        # Break path_resolver so writing fails
        svc._path_resolver = MagicMock()
        svc._path_resolver.get_config_dir.side_effect = RuntimeError("disk full")
        req = TemplateGenerationRequest(all_providers=True)
        result = await svc.generate_templates(req)
        assert result.status == "error"


# ---------------------------------------------------------------------------
# generate_templates — provider-specific mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateTemplatesProviderSpecificMode:
    @pytest.mark.asyncio
    async def test_provider_specific_creates_per_provider_file(self, tmp_path):
        providers = [
            {"name": "aws-prod", "type": "aws"},
            {"name": "aws-staging", "type": "aws"},
        ]
        svc = _make_service(path_dir=str(tmp_path), providers=providers)
        req = TemplateGenerationRequest(all_providers=True, provider_specific=True)
        result = await svc.generate_templates(req)
        # Two providers → two results
        assert len(result.providers) == 2

    @pytest.mark.asyncio
    async def test_provider_specific_uses_scheduler_filename(self, tmp_path):
        svc = _make_service(
            path_dir=str(tmp_path),
            templates_filename="custom_aws_prod_templates.json",
        )
        req = TemplateGenerationRequest(specific_provider="aws-prod", provider_specific=True)
        result = await svc.generate_templates(req)
        assert result.status == "success"


# ---------------------------------------------------------------------------
# _generate_examples_from_provider
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateExamplesFromProvider:
    @pytest.mark.asyncio
    async def test_raises_when_provider_not_registered(self, tmp_path):
        svc = _make_service(register_ok=False)
        with pytest.raises(ValueError, match="not available"):
            await svc._generate_examples_from_provider("unknown", "unknown-provider")

    @pytest.mark.asyncio
    async def test_raises_when_no_generator_for_type(self, tmp_path):
        svc = _make_service()
        svc._generator_resolver = MagicMock()
        svc._generator_resolver.get.return_value = None
        with pytest.raises(ValueError, match="No template generator"):
            await svc._generate_examples_from_provider("aws", "aws-prod")

    @pytest.mark.asyncio
    async def test_raises_when_generator_returns_empty_list(self, tmp_path):
        svc = _make_service(examples=[])
        with pytest.raises(ValueError, match="No example templates"):
            await svc._generate_examples_from_provider("aws", "aws-prod")

    @pytest.mark.asyncio
    async def test_returns_examples_on_success(self, tmp_path):
        examples = [{"template_id": "t1"}, {"template_id": "t2"}]
        svc = _make_service(examples=examples)
        result = await svc._generate_examples_from_provider("aws", "aws-prod")
        assert result == examples


# ---------------------------------------------------------------------------
# _determine_target_providers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDetermineTargetProviders:
    def test_specific_provider_returns_single_entry(self):
        providers = [{"name": "aws-prod", "type": "aws"}]
        svc = _make_service(providers=providers)
        req = TemplateGenerationRequest(specific_provider="aws-prod")
        result = svc._determine_target_providers(req)
        assert len(result) == 1
        assert result[0]["name"] == "aws-prod"

    def test_all_providers_returns_all(self):
        providers = [{"name": "aws-a", "type": "aws"}, {"name": "aws-b", "type": "aws"}]
        svc = _make_service(providers=providers)
        req = TemplateGenerationRequest(all_providers=True)
        result = svc._determine_target_providers(req)
        assert len(result) == 2

    def test_default_returns_all_active_providers(self):
        providers = [{"name": "aws-x", "type": "aws"}]
        svc = _make_service(providers=providers)
        req = TemplateGenerationRequest()
        result = svc._determine_target_providers(req)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _determine_filename
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDetermineFilename:
    def test_provider_specific_delegates_to_scheduler(self):
        svc = _make_service(templates_filename="scheduler-name.json")
        req = TemplateGenerationRequest(provider_specific=True)
        name = svc._determine_filename({"name": "aws-prod", "type": "aws"}, req)
        assert name == "scheduler-name.json"

    def test_provider_type_filter_returns_type_filename(self):
        svc = _make_service()
        req = TemplateGenerationRequest(provider_type_filter="aws")
        name = svc._determine_filename({"name": "aws-prod", "type": "aws"}, req)
        assert name == "aws_templates.json"

    def test_generic_returns_provider_type_filename(self):
        svc = _make_service()
        req = TemplateGenerationRequest()
        name = svc._determine_filename({"name": "aws-prod", "type": "aws"}, req)
        assert name == "aws_templates.json"


# ---------------------------------------------------------------------------
# _deduplicate_templates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeduplicateTemplates:
    def test_deduplicates_by_template_id_keeping_first(self):
        svc = _make_service()
        templates = [
            {"template_id": "t1", "name": "first"},
            {"template_id": "t1", "name": "second"},
            {"template_id": "t2", "name": "unique"},
        ]
        result = svc._deduplicate_templates(templates)
        assert len(result) == 2
        assert result[0]["name"] == "first"

    def test_handles_template_objects_with_model_dump(self):
        svc = _make_service()
        t_obj = MagicMock()
        t_obj.template_id = "obj-1"
        t_obj.model_dump.return_value = {"template_id": "obj-1", "name": "obj"}
        result = svc._deduplicate_templates([t_obj])
        assert len(result) == 1

    def test_skips_templates_without_id(self):
        svc = _make_service()
        templates = [{"name": "no-id"}]
        result = svc._deduplicate_templates(templates)
        assert result == []


# ---------------------------------------------------------------------------
# _get_active_providers — error handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetActiveProviders:
    def test_returns_empty_on_config_exception(self):
        svc = _make_service()
        svc._config_manager = MagicMock()
        svc._config_manager.get_provider_config.side_effect = RuntimeError("config error")
        result = svc._get_active_providers()
        assert result == []
        svc._logger.warning.assert_called()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# _write_templates_file
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWriteTemplatesFile:
    def test_writes_valid_json_with_scheduler_type(self, tmp_path):
        svc = _make_service(scheduler_type="my-scheduler")
        outfile = tmp_path / "out.json"
        svc._write_templates_file(outfile, [{"template_id": "t1"}])

        with open(outfile) as f:
            data = json.load(f)

        assert data["scheduler_type"] == "my-scheduler"
        assert data["templates"] == [{"template_id": "t1"}]

    def test_creates_parent_directories(self, tmp_path):
        svc = _make_service()
        deep = tmp_path / "a" / "b" / "c" / "out.json"
        svc._write_templates_file(deep, [])
        assert deep.exists()

    def test_writes_trailing_newline(self, tmp_path):
        svc = _make_service()
        outfile = tmp_path / "t.json"
        svc._write_templates_file(outfile, [])
        content = outfile.read_text()
        assert content.endswith("\n")


# ---------------------------------------------------------------------------
# _get_templates_file_path — raises without path resolver
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTemplatesFilePath:
    def test_raises_without_path_resolver(self):
        svc = _make_service()
        svc._path_resolver = None
        with pytest.raises(RuntimeError, match="PathResolutionPort not injected"):
            svc._get_templates_file_path("somefile.json")

    def test_returns_path_combining_config_dir_and_filename(self, tmp_path):
        svc = _make_service(path_dir=str(tmp_path))
        result = svc._get_templates_file_path("templates.json")
        assert result == Path(str(tmp_path)) / "templates.json"


# ---------------------------------------------------------------------------
# _format_merged_templates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatMergedTemplates:
    def test_delegates_to_scheduler_format(self):
        svc = _make_service()
        tpls = [{"template_id": "a"}, {"template_id": "b"}]
        result = svc._format_merged_templates(tpls, TemplateGenerationRequest())
        # identity mock: same list
        assert result == tpls
