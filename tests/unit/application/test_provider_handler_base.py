"""Unit tests for application/base/provider_handlers.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.base.provider_handlers import BaseProviderHandler

# ---------------------------------------------------------------------------
# Concrete subclass for testing
# ---------------------------------------------------------------------------


class _SuccessHandler(BaseProviderHandler):
    async def execute_provider_request(self, request: Any) -> Any:
        return {"result": "ok", "input": request}


class _FailingHandler(BaseProviderHandler):
    async def execute_provider_request(self, request: Any) -> Any:
        raise RuntimeError("provider blew up")


class _ValidationFailHandler(BaseProviderHandler):
    async def validate_provider_request(self, request: Any) -> None:
        raise ValueError("bad request")

    async def execute_provider_request(self, request: Any) -> Any:
        return {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseProviderHandlerInit:
    def test_stores_provider_type(self):
        h = _SuccessHandler(provider_type="aws")
        assert h.provider_type == "aws"

    def test_optional_logger_and_error_handler_default_none(self):
        h = _SuccessHandler(provider_type="p1")
        assert h.logger is None
        assert h.error_handler is None

    def test_metrics_starts_empty(self):
        h = _SuccessHandler(provider_type="p1")
        assert h._metrics == {}


@pytest.mark.unit
class TestBaseProviderHandlerSuccess:
    @pytest.mark.asyncio
    async def test_handle_returns_response(self):
        h = _SuccessHandler(provider_type="aws")
        resp = await h.handle("request-payload")
        assert resp["result"] == "ok"

    @pytest.mark.asyncio
    async def test_handle_logs_start_and_success(self):
        logger = MagicMock()
        h = _SuccessHandler(provider_type="aws", logger=logger)
        await h.handle("req")
        assert logger.info.call_count >= 2

    @pytest.mark.asyncio
    async def test_handle_records_success_metrics(self):
        h = _SuccessHandler(provider_type="aws")
        await h.handle("req")
        # Metrics key = provider_type + "_" + request type
        key = "aws_str"
        assert h._metrics[key]["success_count"] == 1
        assert h._metrics[key]["failure_count"] == 0

    @pytest.mark.asyncio
    async def test_multiple_successes_accumulate_count(self):
        h = _SuccessHandler(provider_type="aws")
        await h.handle("r")
        await h.handle("r")
        key = "aws_str"
        assert h._metrics[key]["success_count"] == 2

    @pytest.mark.asyncio
    async def test_avg_duration_computed(self):
        h = _SuccessHandler(provider_type="aws")
        await h.handle("r")
        key = "aws_str"
        assert h._metrics[key]["avg_duration"] >= 0.0


@pytest.mark.unit
class TestBaseProviderHandlerFailure:
    @pytest.mark.asyncio
    async def test_handle_re_raises_on_execute_failure(self):
        h = _FailingHandler(provider_type="aws")
        with pytest.raises(RuntimeError, match="provider blew up"):
            await h.handle("req")

    @pytest.mark.asyncio
    async def test_handle_records_failure_metric(self):
        h = _FailingHandler(provider_type="aws")
        with pytest.raises(RuntimeError):
            await h.handle("req")
        key = "aws_str"
        assert h._metrics[key]["failure_count"] == 1
        assert h._metrics[key]["last_error"] == "provider blew up"

    @pytest.mark.asyncio
    async def test_handle_calls_error_handler_when_provided(self):
        error_handler = MagicMock()
        error_handler.handle_error = AsyncMock()
        h = _FailingHandler(provider_type="aws", error_handler=error_handler)
        with pytest.raises(RuntimeError):
            await h.handle("req")
        error_handler.handle_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_logs_error_when_logger_provided(self):
        logger = MagicMock()
        h = _FailingHandler(provider_type="aws", logger=logger)
        with pytest.raises(RuntimeError):
            await h.handle("req")
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_validation_failure_re_raises(self):
        h = _ValidationFailHandler(provider_type="aws")
        with pytest.raises(ValueError, match="bad request"):
            await h.handle("req")

    @pytest.mark.asyncio
    async def test_none_request_raises_value_error_in_default_validate(self):
        h = _SuccessHandler(provider_type="aws")
        with pytest.raises(ValueError, match="cannot be None"):
            await h.handle(None)  # type: ignore[arg-type]


@pytest.mark.unit
class TestBaseProviderHandlerMetricsMethods:
    def test_record_success_creates_key_if_missing(self):
        h = _SuccessHandler(provider_type="p")
        h._record_success_metrics("MyRequest", 0.01)
        assert "p_MyRequest" in h._metrics
        assert h._metrics["p_MyRequest"]["success_count"] == 1

    def test_record_failure_stores_last_error(self):
        h = _SuccessHandler(provider_type="p")
        h._record_failure_metrics("MyRequest", 0.02, ValueError("oops"))
        assert h._metrics["p_MyRequest"]["failure_count"] == 1
        assert "oops" in h._metrics["p_MyRequest"]["last_error"]

    def test_get_metrics_returns_copy(self):
        h = _SuccessHandler(provider_type="p")
        h._record_success_metrics("T", 0.1)
        m1 = h.get_metrics()
        # Adding a new key to the outer copy should not affect the original dict
        m1["new_key"] = "injected"
        assert "new_key" not in h._metrics

    def test_avg_duration_mixed_success_and_failure(self):
        h = _SuccessHandler(provider_type="p")
        h._record_success_metrics("T", 0.10)
        h._record_failure_metrics("T", 0.20, RuntimeError("x"))
        m = h._metrics["p_T"]
        # total 2 calls, total_duration ~ 0.30
        assert m["avg_duration"] == pytest.approx(0.15, abs=1e-9)
