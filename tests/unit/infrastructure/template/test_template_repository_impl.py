"""Unit tests for TemplateRepositoryImpl.

Coverage targets: lines 18,27-29,31-35,43-44,49-51,55-57,61-62,66-68,73,
77-79,85-87,91-99,104,108,112,116-119,126
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.domain.template.template_aggregate import Template
from orb.infrastructure.template.dtos import TemplateDTO
from orb.infrastructure.template.template_repository_impl import (
    TemplateRepositoryImpl,
    _dto_to_template,
    _run_async,
    create_template_repository_impl,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dto(template_id: str = "t-1", name: str = "Test Template") -> TemplateDTO:
    return TemplateDTO(template_id=template_id, name=name, provider_api="EC2Fleet")


def _make_logger() -> MagicMock:
    m = MagicMock()
    m.debug = MagicMock()
    m.info = MagicMock()
    m.error = MagicMock()
    return m


def _make_manager(**kwargs) -> MagicMock:
    mgr = MagicMock()
    mgr.get_template = MagicMock(**kwargs)
    mgr.get_all_templates_sync = MagicMock(return_value=[])
    mgr.save_template = AsyncMock()
    mgr.delete_template = AsyncMock()
    mgr.validate_template = AsyncMock(return_value={"is_valid": True, "errors": []})
    return mgr


def _make_repo(**mgr_kwargs) -> tuple[TemplateRepositoryImpl, MagicMock, MagicMock]:
    logger = _make_logger()
    mgr = _make_manager(**mgr_kwargs)
    repo = TemplateRepositoryImpl(mgr, logger)
    return repo, mgr, logger


# ---------------------------------------------------------------------------
# _dto_to_template
# ---------------------------------------------------------------------------


class TestDtoToTemplate:
    def test_converts_dto_to_template_with_correct_fields(self):
        dto = _make_dto("t-99", "My Template")
        t = _dto_to_template(dto)
        assert isinstance(t, Template)
        assert t.template_id == "t-99"
        assert t.provider_api == "EC2Fleet"


# ---------------------------------------------------------------------------
# _run_async
# ---------------------------------------------------------------------------


class TestRunAsync:
    def test_run_async_without_running_loop(self):
        async def coro() -> int:
            return 42

        result = _run_async(coro())
        assert result == 42

    def test_run_async_inside_running_loop_uses_thread(self):
        async def coro() -> str:
            return "inner"

        async def driver():
            return _run_async(coro())

        result = asyncio.run(driver())
        assert result == "inner"


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------


class TestSave:
    def test_save_calls_manager_save_template(self):
        repo, mgr, _ = _make_repo()
        template = Template(template_id="t-1", name="T", provider_api="EC2Fleet")
        repo.save(template)
        mgr.save_template.assert_called_once()

    def test_save_logs_debug(self):
        repo, _, logger = _make_repo()
        template = Template(template_id="t-2", name="T", provider_api="EC2Fleet")
        repo.save(template)
        logger.debug.assert_called()


# ---------------------------------------------------------------------------
# find_by_id
# ---------------------------------------------------------------------------


class TestFindById:
    def test_returns_template_when_found(self):
        dto = _make_dto("t-1")
        repo, _, _ = _make_repo(return_value=dto)
        result = repo.find_by_id("t-1")
        assert result is not None
        assert result.template_id == "t-1"

    def test_returns_none_when_not_found(self):
        repo, _, _ = _make_repo(return_value=None)
        result = repo.find_by_id("missing")
        assert result is None


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_calls_manager_delete_template(self):
        repo, mgr, _ = _make_repo()
        repo.delete("t-1")
        mgr.delete_template.assert_called_once_with("t-1")


# ---------------------------------------------------------------------------
# find_all
# ---------------------------------------------------------------------------


class TestFindAll:
    def test_returns_empty_when_no_templates(self):
        repo, _, _ = _make_repo()
        result = repo.find_all()
        assert result == []

    def test_returns_converted_templates(self):
        dto1 = _make_dto("t-1")
        dto2 = _make_dto("t-2")
        repo, mgr, _ = _make_repo()
        mgr.get_all_templates_sync.return_value = [dto1, dto2]
        result = repo.find_all()
        assert len(result) == 2
        assert result[0].template_id == "t-1"


# ---------------------------------------------------------------------------
# find_by_template_id
# ---------------------------------------------------------------------------


class TestFindByTemplateId:
    def test_delegates_to_find_by_id(self):
        dto = _make_dto("t-5")
        repo, mgr, _ = _make_repo(return_value=dto)
        result = repo.find_by_template_id("t-5")
        assert result is not None
        assert result.template_id == "t-5"
        mgr.get_template.assert_called_once_with("t-5")


# ---------------------------------------------------------------------------
# find_by_provider_api
# ---------------------------------------------------------------------------


class TestFindByProviderApi:
    def test_filters_by_provider_api(self):
        dto_match = _make_dto("t-1")
        dto_other = TemplateDTO(template_id="t-2", name="Other", provider_api="K8s")
        repo, mgr, _ = _make_repo()
        mgr.get_all_templates_sync.return_value = [dto_match, dto_other]
        result = repo.find_by_provider_api("EC2Fleet")
        assert len(result) == 1
        assert result[0].template_id == "t-1"

    def test_returns_empty_when_no_match(self):
        dto = _make_dto("t-1")
        repo, mgr, _ = _make_repo()
        mgr.get_all_templates_sync.return_value = [dto]
        result = repo.find_by_provider_api("NoSuchApi")
        assert result == []


# ---------------------------------------------------------------------------
# find_active_templates
# ---------------------------------------------------------------------------


class TestFindActiveTemplates:
    def test_returns_all_templates(self):
        dtos = [_make_dto("t-1"), _make_dto("t-2")]
        repo, mgr, _ = _make_repo()
        mgr.get_all_templates_sync.return_value = dtos
        result = repo.find_active_templates()
        assert len(result) == 2


# ---------------------------------------------------------------------------
# search_templates
# ---------------------------------------------------------------------------


class TestSearchTemplates:
    def test_matches_criteria(self):
        dto = _make_dto("t-1")
        repo, mgr, _ = _make_repo()
        mgr.get_all_templates_sync.return_value = [dto]
        result = repo.search_templates({"template_id": "t-1"})
        assert len(result) == 1

    def test_no_match_returns_empty(self):
        dto = _make_dto("t-1")
        repo, mgr, _ = _make_repo()
        mgr.get_all_templates_sync.return_value = [dto]
        result = repo.search_templates({"template_id": "other"})
        assert result == []


# ---------------------------------------------------------------------------
# convenience methods
# ---------------------------------------------------------------------------


class TestConvenienceMethods:
    def test_get_by_id_delegates_to_find_by_id(self):
        dto = _make_dto("t-3")
        repo, mgr, _ = _make_repo(return_value=dto)
        result = repo.get_by_id("t-3")
        assert result is not None
        mgr.get_template.assert_called_once_with("t-3")

    def test_get_all_returns_active_templates(self):
        dto = _make_dto("t-4")
        repo, mgr, _ = _make_repo()
        mgr.get_all_templates_sync.return_value = [dto]
        result = repo.get_all()
        assert len(result) == 1

    def test_exists_true_when_template_found(self):
        dto = _make_dto("t-1")
        repo, mgr, _ = _make_repo(return_value=dto)
        assert repo.exists("t-1") is True

    def test_exists_false_when_not_found(self):
        repo, _, _ = _make_repo(return_value=None)
        assert repo.exists("missing") is False


# ---------------------------------------------------------------------------
# validate_template
# ---------------------------------------------------------------------------


class TestValidateTemplate:
    def test_returns_empty_when_valid(self):
        repo, mgr, _ = _make_repo()
        mgr.validate_template = AsyncMock(return_value={"is_valid": True, "errors": []})
        template = Template(template_id="t-1", name="T", provider_api="EC2Fleet")
        result = repo.validate_template(template)
        assert result == []

    def test_returns_errors_when_invalid(self):
        repo, mgr, _ = _make_repo()
        mgr.validate_template = AsyncMock(
            return_value={"is_valid": False, "errors": ["Missing provider_api"]}
        )
        template = Template(template_id="t-1", name="T", provider_api="EC2Fleet")
        result = repo.validate_template(template)
        assert "Missing provider_api" in result


# ---------------------------------------------------------------------------
# create_template_repository_impl
# ---------------------------------------------------------------------------


class TestCreateTemplateRepositoryImpl:
    def test_factory_function_returns_impl(self):
        mgr = _make_manager()
        logger = _make_logger()
        repo = create_template_repository_impl(mgr, logger)
        assert isinstance(repo, TemplateRepositoryImpl)
