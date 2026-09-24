"""Unit tests for application/queries/template_query_handlers.py — extended coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import (
    GetTemplateQuery,
    ListTemplatesQuery,
    ValidateTemplateQuery,
)
from orb.application.queries.template_query_handlers import (
    GetConfigurationHandler,
    GetTemplateHandler,
    ListTemplatesHandler,
    ValidateTemplateHandler,
)
from orb.domain.base.exceptions import EntityNotFoundError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger():
    return MagicMock()


def _make_error_handler():
    return MagicMock()


def _make_template_dto(template_id="tmpl-1", name="Test Template", is_active=True):
    dto = MagicMock()
    dto.template_id = template_id
    dto.name = name
    dto.provider_api = "EC2Fleet"
    dto.is_active = is_active

    def _model_dump(**kwargs):
        return {
            "template_id": template_id,
            "name": name,
            "provider_api": "EC2Fleet",
            "is_active": is_active,
        }

    dto.model_dump = _model_dump
    return dto


def _make_container_with_template_port(
    template_dto=None, template_dtos=None, validation_errors=None, has_defaults_service=False
):
    container = MagicMock()
    template_port = MagicMock()

    template_port.get_template_by_id = AsyncMock(return_value=template_dto)
    template_port.load_templates = AsyncMock(return_value=template_dtos or [])
    template_port.get_templates_by_provider = AsyncMock(return_value=template_dtos or [])
    template_port.validate_template_config = MagicMock(return_value=validation_errors or [])

    container.has.return_value = has_defaults_service
    container.get.return_value = template_port
    return container, template_port


# ---------------------------------------------------------------------------
# GetTemplateHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTemplateHandler:
    def _handler(self, template_dto=None):
        container, _ = _make_container_with_template_port(template_dto=template_dto)
        template_factory = MagicMock()
        template_factory.create_template.return_value = MagicMock()

        template_dto_factory = MagicMock()
        template_dto_factory.from_domain.return_value = MagicMock(template_id="tmpl-1")

        return GetTemplateHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            container=container,
            template_factory=template_factory,
            template_dto_factory=template_dto_factory,
        )

    @pytest.mark.asyncio
    async def test_maps_loaded_template_through_factories(self):
        # Verify the wiring, not the mock's echo: the loaded DTO's data must flow
        # into template_factory.create_template, its domain output into
        # template_dto_factory.from_domain, and that result is returned unchanged.
        dto = _make_template_dto("tmpl-1")
        container, _ = _make_container_with_template_port(template_dto=dto)

        domain_template = object()
        template_factory = MagicMock()
        template_factory.create_template.return_value = domain_template

        final_dto = object()
        template_dto_factory = MagicMock()
        template_dto_factory.from_domain.return_value = final_dto

        h = GetTemplateHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            container=container,
            template_factory=template_factory,
            template_dto_factory=template_dto_factory,
        )

        result = await h.execute_query(GetTemplateQuery(template_id="tmpl-1"))

        # create_template receives the resolved dict built from the loaded DTO.
        template_factory.create_template.assert_called_once()
        (passed_data,) = template_factory.create_template.call_args.args
        assert passed_data["template_id"] == "tmpl-1"
        assert passed_data["name"] == "Test Template"
        assert passed_data["provider_api"] == "EC2Fleet"

        # from_domain receives the domain template create_template produced.
        template_dto_factory.from_domain.assert_called_once_with(domain_template)

        # The handler returns exactly the factory output.
        assert result is final_dto

    @pytest.mark.asyncio
    async def test_raises_not_found_when_missing(self):
        h = self._handler(template_dto=None)
        q = GetTemplateQuery(template_id="tmpl-missing")
        with pytest.raises(EntityNotFoundError):
            await h.execute_query(q)

    @pytest.mark.asyncio
    async def test_logs_error_on_not_found(self):
        logger = _make_logger()
        container, _ = _make_container_with_template_port(template_dto=None)
        h = GetTemplateHandler(
            logger=logger,
            error_handler=_make_error_handler(),
            container=container,
            template_factory=MagicMock(),
            template_dto_factory=MagicMock(),
        )
        q = GetTemplateQuery(template_id="tmpl-x")
        with pytest.raises(EntityNotFoundError):
            await h.execute_query(q)
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_generic_exception_propagates(self):
        container = MagicMock()
        container.get.side_effect = RuntimeError("container broken")
        h = GetTemplateHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            container=container,
            template_factory=MagicMock(),
            template_dto_factory=MagicMock(),
        )
        q = GetTemplateQuery(template_id="tmpl-1")
        with pytest.raises(RuntimeError, match="container broken"):
            await h.execute_query(q)


# ---------------------------------------------------------------------------
# ListTemplatesHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListTemplatesHandler:
    def _handler(self, template_dtos=None, filter_svc=None):
        container, _ = _make_container_with_template_port(template_dtos=template_dtos)
        if filter_svc is None:
            filter_svc = MagicMock()
            filter_svc.apply_filters.side_effect = lambda items, _: items
        return ListTemplatesHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            container=container,
            generic_filter_service=filter_svc,
        )

    @pytest.mark.asyncio
    async def test_returns_all_templates(self):
        dtos = [_make_template_dto("t1"), _make_template_dto("t2")]
        h = self._handler(template_dtos=dtos)
        q = ListTemplatesQuery(limit=None)
        result = await h.execute_query(q)
        assert result.total_count == 2

    @pytest.mark.asyncio
    async def test_active_only_filter(self):
        dtos = [
            _make_template_dto("t1", is_active=True),
            _make_template_dto("t2", is_active=False),
        ]
        h = self._handler(template_dtos=dtos)
        q = ListTemplatesQuery(active_only=True, limit=None)
        result = await h.execute_query(q)
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_q_filter(self):
        dtos = [
            _make_template_dto("tmpl-abc", name="alpha"),
            _make_template_dto("tmpl-xyz", name="xenon"),
        ]
        h = self._handler(template_dtos=dtos)
        q = ListTemplatesQuery(q="alpha", limit=None)
        result = await h.execute_query(q)
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_sort_ascending(self):
        dtos = [
            _make_template_dto("t2", name="beta"),
            _make_template_dto("t1", name="alpha"),
        ]
        h = self._handler(template_dtos=dtos)
        q = ListTemplatesQuery(sort="+name", limit=None)
        result = await h.execute_query(q)
        assert result.items[0].name == "alpha"

    @pytest.mark.asyncio
    async def test_sort_descending(self):
        dtos = [
            _make_template_dto("t1", name="alpha"),
            _make_template_dto("t2", name="beta"),
        ]
        h = self._handler(template_dtos=dtos)
        q = ListTemplatesQuery(sort="-name", limit=None)
        result = await h.execute_query(q)
        assert result.items[0].name == "beta"

    @pytest.mark.asyncio
    async def test_limit_and_offset(self):
        dtos = [_make_template_dto(f"t{i}") for i in range(10)]
        h = self._handler(template_dtos=dtos)
        q = ListTemplatesQuery(limit=3, offset=2)
        result = await h.execute_query(q)
        assert len(result.items) == 3
        assert result.total_count == 10

    @pytest.mark.asyncio
    async def test_none_limit_no_cap(self):
        dtos = [_make_template_dto(f"t{i}") for i in range(7)]
        h = self._handler(template_dtos=dtos)
        q = ListTemplatesQuery(limit=None)
        result = await h.execute_query(q)
        assert len(result.items) == 7

    @pytest.mark.asyncio
    async def test_limit_clamped_to_1000(self):
        dtos = [_make_template_dto(f"t{i}") for i in range(5)]
        h = self._handler(template_dtos=dtos)
        q = ListTemplatesQuery(limit=9999)
        result = await h.execute_query(q)
        assert len(result.items) == 5

    @pytest.mark.asyncio
    async def test_zero_limit_returns_empty(self):
        dtos = [_make_template_dto("t1")]
        h = self._handler(template_dtos=dtos)
        q = ListTemplatesQuery(limit=0)
        result = await h.execute_query(q)
        assert result.items == []
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_provider_name_load_path(self):
        container = MagicMock()
        port = MagicMock()
        port.load_templates = AsyncMock(return_value=[_make_template_dto("t1")])
        container.get.return_value = port
        filter_svc_pn = MagicMock()
        filter_svc_pn.apply_filters.side_effect = lambda items, _: items
        h = ListTemplatesHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            container=container,
            generic_filter_service=filter_svc_pn,
        )
        q = ListTemplatesQuery(provider_name="aws", limit=None)
        await h.execute_query(q)
        port.load_templates.assert_called_with(provider_override="aws")

    @pytest.mark.asyncio
    async def test_provider_api_load_path(self):
        container = MagicMock()
        port = MagicMock()
        port.get_templates_by_provider = AsyncMock(return_value=[_make_template_dto("t1")])
        container.get.return_value = port
        filter_svc_pa = MagicMock()
        filter_svc_pa.apply_filters.side_effect = lambda items, _: items
        h = ListTemplatesHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            container=container,
            generic_filter_service=filter_svc_pa,
        )
        q = ListTemplatesQuery(provider_api="SpotFleet", limit=None)
        await h.execute_query(q)
        port.get_templates_by_provider.assert_called_with("SpotFleet")

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        container = MagicMock()
        container.get.side_effect = RuntimeError("broken")
        h = ListTemplatesHandler(
            logger=_make_logger(),
            error_handler=_make_error_handler(),
            container=container,
            generic_filter_service=MagicMock(),
        )
        q = ListTemplatesQuery()
        with pytest.raises(RuntimeError, match="broken"):
            await h.execute_query(q)


# ---------------------------------------------------------------------------
# ValidateTemplateHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateTemplateHandler:
    def _handler(self, template_dto=None, validation_errors=None):
        container, _ = _make_container_with_template_port(
            template_dto=template_dto, validation_errors=validation_errors or []
        )
        return ValidateTemplateHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
        )

    @pytest.mark.asyncio
    async def test_valid_template_config(self):
        # Provide template_config with more than just template_id so it
        # goes to the direct validation path (not load-by-id path)
        h = self._handler(validation_errors=[])
        q = ValidateTemplateQuery(
            template_config={"template_id": "t1", "name": "test", "image_id": "ami-abc"}
        )
        result = await h.execute_query(q)
        assert result["success"] is True
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_invalid_template_config(self):
        h = self._handler(validation_errors=["missing required field: image_id"])
        # Use template_config with extra fields so it goes to validation path directly
        q = ValidateTemplateQuery(
            template_config={"template_id": "t1", "name": "test", "extra": True}
        )
        result = await h.execute_query(q)
        assert result["success"] is False
        assert result["valid"] is False
        assert "missing required field" in result["validation_errors"][0]

    @pytest.mark.asyncio
    async def test_loads_template_by_id_when_no_config(self):
        dto = _make_template_dto("tmpl-1")
        h = self._handler(template_dto=dto, validation_errors=[])
        q = ValidateTemplateQuery(template_id="tmpl-1", template_config={})
        result = await h.execute_query(q)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_template_not_found_raises(self):
        h = self._handler(template_dto=None)
        q = ValidateTemplateQuery(template_id="tmpl-missing", template_config={})
        with pytest.raises(EntityNotFoundError):
            await h.execute_query(q)

    @pytest.mark.asyncio
    async def test_exception_during_load_returns_error(self):
        container = MagicMock()
        port = MagicMock()
        port.get_template_by_id = AsyncMock(side_effect=RuntimeError("load failed"))
        port.validate_template_config = MagicMock(return_value=[])
        container.get.return_value = port

        h = ValidateTemplateHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
        )
        q = ValidateTemplateQuery(template_id="t1", template_config={})
        result = await h.execute_query(q)
        # Correct contract: a load failure yields an error dict, not a raised exception.
        assert result["success"] is False
        assert result["valid"] is False
        assert "Failed to load template" in result["message"]
        assert "load failed" in result["message"]

    @pytest.mark.asyncio
    async def test_exception_during_validate_returns_error_response(self):
        container = MagicMock()
        port = MagicMock()
        port.validate_template_config = MagicMock(side_effect=RuntimeError("validator gone"))
        container.get.return_value = port

        h = ValidateTemplateHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
        )
        q = ValidateTemplateQuery(template_config={"template_id": "t1", "name": "test"})
        result = await h.execute_query(q)
        assert result["success"] is False


# ---------------------------------------------------------------------------
# GetConfigurationHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetConfigurationHandler:
    @pytest.mark.asyncio
    async def test_returns_config_value(self):
        container = MagicMock()
        cfg = MagicMock()
        cfg.get_configuration_value.return_value = "myvalue"
        container.get.return_value = cfg

        from orb.application.dto.queries import GetConfigurationQuery

        h = GetConfigurationHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
        )
        q = GetConfigurationQuery(key="storage.strategy", default=None)
        result = await h.execute_query(q)
        assert result["key"] == "storage.strategy"
        assert result["value"] == "myvalue"

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        container = MagicMock()
        container.get.side_effect = RuntimeError("no cfg")

        from orb.application.dto.queries import GetConfigurationQuery

        h = GetConfigurationHandler(
            logger=_make_logger(),
            container=container,
            error_handler=_make_error_handler(),
        )
        q = GetConfigurationQuery(key="x")
        with pytest.raises(RuntimeError, match="no cfg"):
            await h.execute_query(q)
