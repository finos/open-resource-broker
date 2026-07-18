"""Shared fixtures for providers/base/strategy tests."""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.interfaces.provider import BaseProviderConfig
from orb.providers.base.strategy.provider_strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)


class ConcreteProviderStrategy(ProviderStrategy):
    """Minimal concrete ProviderStrategy for use in unit tests.

    Behaviour is configured per-instance so each test can control whether
    operations succeed, raise, or return custom data without class-level
    state leaking between tests.
    """

    def __init__(
        self,
        provider_type: str = "fake",
        *,
        operation_result: Optional[ProviderResult] = None,
        operation_raises: Optional[Exception] = None,
        supported_ops: Optional[list[ProviderOperationType]] = None,
        healthy: bool = True,
    ) -> None:
        config = BaseProviderConfig(provider_type=provider_type)
        super().__init__(config)
        self._provider_type = provider_type
        self._operation_result = operation_result or ProviderResult.success_result({"status": "ok"})
        self._operation_raises = operation_raises
        self._supported_ops = supported_ops or list(ProviderOperationType)
        self._healthy = healthy

    @property
    def provider_type(self) -> str:
        return self._provider_type

    def initialize(self) -> bool:
        self._initialized = True
        return True

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        if self._operation_raises is not None:
            raise self._operation_raises
        return self._operation_result

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type=self._provider_type,
            supported_operations=self._supported_ops,
            features={"test": True},
            limitations={},
        )

    def check_health(self) -> ProviderHealthStatus:
        if self._healthy:
            return ProviderHealthStatus.healthy("All good", response_time_ms=1.0)
        return ProviderHealthStatus.unhealthy("Broken", {"reason": "test"})

    def generate_provider_name(self, config: dict[str, Any]) -> str:
        return self._provider_type

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        return {"provider_type": provider_name}

    def get_provider_name_pattern(self) -> str:
        return self._provider_type

    def cleanup(self) -> None:
        self._initialized = False


def make_op(
    op_type: ProviderOperationType = ProviderOperationType.HEALTH_CHECK,
    params: Optional[dict] = None,
) -> ProviderOperation:
    """Helper: build a ProviderOperation value object."""
    return ProviderOperation(operation_type=op_type, parameters=params or {})


def run_async(coro):
    """Run an async coroutine in a fresh event loop (test helper)."""
    return asyncio.run(coro)


@pytest.fixture
def mock_logger() -> MagicMock:
    return MagicMock(spec=LoggingPort)


@pytest.fixture
def fake_strategy(mock_logger) -> ConcreteProviderStrategy:
    """A single initialised fake strategy."""
    s = ConcreteProviderStrategy(provider_type="fake_a")
    s.initialize()
    return s


@pytest.fixture
def fake_strategy_b(mock_logger) -> ConcreteProviderStrategy:
    s = ConcreteProviderStrategy(provider_type="fake_b")
    s.initialize()
    return s


@pytest.fixture
def health_op() -> ProviderOperation:
    return make_op(ProviderOperationType.HEALTH_CHECK)
