"""Unit tests for templates_generate_handler — all branch paths."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.domain.base.ports.console_port import ConsolePort

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _make_provider_result(status="created", filename="aws.json", templates_count=3):
    from orb.application.dto.template_generation_dto import ProviderTemplateResult

    return ProviderTemplateResult(
        provider="aws",
        filename=filename,
        templates_count=templates_count,
        path="/tmp/aws.json",
        status=status,
        reason="",
    )


def _make_generation_result(
    status="success",
    message="Generated 3 templates",
    providers=None,
    total_templates=3,
    created_count=3,
    skipped_count=0,
):
    from orb.application.dto.template_generation_dto import TemplateGenerationResult

    return TemplateGenerationResult(
        status=status,
        message=message,
        providers=providers or [],
        total_templates=total_templates,
        created_count=created_count,
        skipped_count=skipped_count,
    )


def _make_container(service_mock=None, console_mock=None):
    from orb.application.services.template_generation_service import TemplateGenerationService

    container = MagicMock()
    mock_svc = service_mock or MagicMock(spec=TemplateGenerationService)
    mock_console = console_mock or MagicMock(spec=ConsolePort)

    def _get(cls):
        if cls is TemplateGenerationService:
            return mock_svc
        if cls is ConsolePort:
            return mock_console
        return MagicMock()

    container.get.side_effect = _get
    return container, mock_svc, mock_console


# ---------------------------------------------------------------------------
# handle_templates_generate — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleTemplatesGenerate:
    @pytest.mark.asyncio
    async def test_happy_path_returns_status_dict(self):
        """Successful generation returns a dict with 'status' and 'providers'."""
        from orb.interface.templates_generate_handler import handle_templates_generate

        created = _make_provider_result(status="created", filename="aws.json", templates_count=5)
        result = _make_generation_result(
            status="success",
            message="Generated 5 templates",
            providers=[created],
            total_templates=5,
            created_count=5,
        )

        container, mock_svc, _ = _make_container()
        mock_svc.generate_templates = AsyncMock(return_value=result)

        args = _make_args()
        args._container = container

        out = await handle_templates_generate(args)

        assert out["status"] == "success"
        assert len(out["providers"]) == 1
        assert out["providers"][0]["provider"] == "aws"
        assert out["total_templates"] == 5
        assert out["created_count"] == 5

    @pytest.mark.asyncio
    async def test_skipped_files_list_populated(self):
        """Providers with status='skipped' appear in skipped_files list."""
        from orb.interface.templates_generate_handler import handle_templates_generate

        skipped = _make_provider_result(status="skipped", filename="aws.json")
        result = _make_generation_result(providers=[skipped], created_count=0)

        container, mock_svc, _ = _make_container()
        mock_svc.generate_templates = AsyncMock(return_value=result)

        args = _make_args()
        args._container = container

        out = await handle_templates_generate(args)

        assert "aws.json" in out["skipped_files"]

    @pytest.mark.asyncio
    async def test_provider_specific_args_forwarded_to_service(self):
        """provider_name, all_providers, force are forwarded via TemplateGenerationRequest."""
        from orb.application.dto.template_generation_dto import TemplateGenerationRequest
        from orb.interface.templates_generate_handler import handle_templates_generate

        result = _make_generation_result(providers=[])
        container, mock_svc, _ = _make_container()
        mock_svc.generate_templates = AsyncMock(return_value=result)

        args = _make_args(
            provider_name="aws-prod",
            all_providers=True,
            provider_api="EC2Fleet",
            provider_specific=True,
            provider_type="aws",
            force=True,
        )
        args._container = container

        await handle_templates_generate(args)

        mock_svc.generate_templates.assert_awaited_once()
        req: TemplateGenerationRequest = mock_svc.generate_templates.call_args[0][0]
        assert req.specific_provider == "aws-prod"
        assert req.all_providers is True
        assert req.provider_api == "EC2Fleet"
        assert req.provider_specific is True
        assert req.provider_type_filter == "aws"
        assert req.force_overwrite is True

    @pytest.mark.asyncio
    async def test_no_args_attrs_uses_defaults(self):
        """Missing optional attrs fall back to None/False defaults."""
        from orb.application.dto.template_generation_dto import TemplateGenerationRequest
        from orb.interface.templates_generate_handler import handle_templates_generate

        result = _make_generation_result(providers=[])
        container, mock_svc, _ = _make_container()
        mock_svc.generate_templates = AsyncMock(return_value=result)

        args = _make_args()
        args._container = container

        await handle_templates_generate(args)

        req: TemplateGenerationRequest = mock_svc.generate_templates.call_args[0][0]
        assert req.specific_provider is None
        assert req.all_providers is False
        assert req.force_overwrite is False


# ---------------------------------------------------------------------------
# handle_templates_generate — error path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleTemplatesGenerateErrors:
    @pytest.mark.asyncio
    async def test_exception_returns_error_dict(self):
        """If generate_templates raises, the handler returns an error dict."""
        from orb.interface.templates_generate_handler import handle_templates_generate

        container, mock_svc, _ = _make_container()
        mock_svc.generate_templates = AsyncMock(side_effect=RuntimeError("service unavailable"))

        args = _make_args()
        args._container = container

        out = await handle_templates_generate(args)

        assert out["status"] == "error"
        assert out["success"] is False
        assert "service unavailable" in out["error"]

    @pytest.mark.asyncio
    async def test_container_get_exception_returns_error_dict(self):
        """If DI container.get raises, the handler returns an error dict."""
        from orb.interface.templates_generate_handler import handle_templates_generate

        container = MagicMock()
        container.get.side_effect = KeyError("not registered")

        args = _make_args()
        args._container = container

        out = await handle_templates_generate(args)

        assert out["status"] == "error"
        assert out["success"] is False


# ---------------------------------------------------------------------------
# _print_generation_results — console output branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPrintGenerationResults:
    def test_error_status_returns_immediately_no_console_call(self):
        """status='error' → _print_generation_results returns early, no console output."""
        from orb.interface.templates_generate_handler import _print_generation_results

        result = _make_generation_result(status="error", providers=[])
        container, _, mock_console = _make_container()

        _print_generation_results(result, container)

        mock_console.info.assert_not_called()
        mock_console.success.assert_not_called()

    def test_created_providers_calls_console_success(self):
        """created providers → console.success called with result.message."""
        from orb.interface.templates_generate_handler import _print_generation_results

        created = _make_provider_result(status="created")
        result = _make_generation_result(
            status="success",
            message="Generated 3 templates",
            providers=[created],
            total_templates=3,
            created_count=3,
        )

        container, _, mock_console = _make_container()

        _print_generation_results(result, container)

        mock_console.success.assert_called_once_with("Generated 3 templates")

    def test_skipped_providers_prints_skipped_message(self):
        """skipped providers → info message about skipped files is printed."""
        from orb.interface.templates_generate_handler import _print_generation_results

        skipped = _make_provider_result(status="skipped", filename="old.json")
        result = _make_generation_result(
            status="success",
            providers=[skipped],
            created_count=0,
        )

        container, _, mock_console = _make_container()

        _print_generation_results(result, container)

        # Should have printed info about skipped files
        calls = [str(c) for c in mock_console.info.call_args_list]
        combined = " ".join(calls)
        assert "old.json" in combined or "Skipped" in combined

    def test_no_created_no_skipped_prints_nothing(self):
        """No providers at all → prints 'No templates generated'."""
        from orb.interface.templates_generate_handler import _print_generation_results

        result = _make_generation_result(
            status="success",
            providers=[],
            created_count=0,
        )

        container, _, mock_console = _make_container()

        _print_generation_results(result, container)

        mock_console.info.assert_called()

    def test_only_skipped_no_created_prints_no_new_templates(self):
        """Only skipped, no created → 'No new templates generated' message."""
        from orb.interface.templates_generate_handler import _print_generation_results

        skipped = _make_provider_result(status="skipped")
        result = _make_generation_result(
            status="success",
            providers=[skipped],
            created_count=0,
        )

        container, _, mock_console = _make_container()

        _print_generation_results(result, container)

        calls = [str(c) for c in mock_console.info.call_args_list]
        combined = " ".join(calls)
        assert "No new templates" in combined or "already exist" in combined
