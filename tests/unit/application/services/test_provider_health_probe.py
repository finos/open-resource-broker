"""ProviderRegistryService.probe_strategy_health: real, bounded, cached provider health."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest

from orb.application.services.provider_registry_service import ProviderRegistryService
from orb.providers.base.strategy.provider_strategy import ProviderHealthStatus


def _service(strategy) -> tuple[ProviderRegistryService, MagicMock]:
    registry = MagicMock()
    registry.get_or_create_strategy.return_value = strategy
    logger = MagicMock()
    return ProviderRegistryService(registry, MagicMock(), logger), logger


def _strategy(health=None, side_effect=None):
    s = MagicMock()
    if side_effect is not None:
        s.check_health.side_effect = side_effect
    else:
        s.check_health.return_value = health
    return s


@pytest.mark.unit
class TestProbeStrategyHealth:
    @pytest.mark.asyncio
    async def test_healthy_provider(self):
        svc, _ = _service(_strategy(ProviderHealthStatus.healthy("account 123456789012 ok")))
        snap = await svc.probe_strategy_health("aws")
        assert snap.status == "healthy"
        assert snap.message == "Provider is healthy"  # provider text is never forwarded
        assert snap.sample_count == 1

    @pytest.mark.asyncio
    async def test_degraded_provider_reports_unhealthy_with_generic_message(self):
        svc, logger = _service(
            _strategy(ProviderHealthStatus.unhealthy("AccessDenied for arn:aws:iam::123456789012"))
        )
        snap = await svc.probe_strategy_health("aws")
        assert snap.status == "unhealthy"
        assert "123456789012" not in snap.message
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_probe_exception_is_unhealthy_and_not_raised(self):
        svc, _ = _service(_strategy(side_effect=RuntimeError("boom 123456789012")))
        snap = await svc.probe_strategy_health("aws")
        assert snap.status == "unhealthy"
        assert "boom" not in snap.message

    @pytest.mark.asyncio
    async def test_hung_probe_times_out(self):
        release = threading.Event()

        def hang():
            release.wait(2)
            return ProviderHealthStatus.healthy()

        svc, _ = _service(_strategy(side_effect=hang))
        try:
            snap = await svc.probe_strategy_health("aws", timeout=0.05)
        finally:
            release.set()
        assert snap.status == "unhealthy"
        assert "timed out" in snap.message

    @pytest.mark.asyncio
    async def test_missing_strategy_is_unknown_not_healthy(self):
        svc, _ = _service(None)
        snap = await svc.probe_strategy_health("nope")
        assert snap.status == "unknown"

    @pytest.mark.asyncio
    async def test_health_changes_when_provider_degrades(self):
        strategy = _strategy(ProviderHealthStatus.healthy())
        svc, _ = _service(strategy)
        assert (await svc.probe_strategy_health("aws", ttl=0)).status == "healthy"
        strategy.check_health.return_value = ProviderHealthStatus.unhealthy("down")
        snap = await svc.probe_strategy_health("aws", ttl=0)
        assert snap.status == "unhealthy"
        assert snap.sample_count == 2

    @pytest.mark.asyncio
    async def test_result_is_cached_within_ttl(self):
        strategy = _strategy(ProviderHealthStatus.healthy())
        svc, _ = _service(strategy)
        await svc.probe_strategy_health("aws", ttl=30)
        await svc.probe_strategy_health("aws", ttl=30)
        assert strategy.check_health.call_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_callers_share_one_probe(self):
        def slow():
            time.sleep(0.05)
            return ProviderHealthStatus.healthy()

        strategy = _strategy(side_effect=slow)
        svc, _ = _service(strategy)
        results = await asyncio.gather(*(svc.probe_strategy_health("aws") for _ in range(5)))
        assert strategy.check_health.call_count == 1
        assert {r.status for r in results} == {"healthy"}

    @pytest.mark.asyncio
    async def test_probe_does_not_block_the_event_loop(self):
        def slow():
            time.sleep(0.2)
            return ProviderHealthStatus.healthy()

        svc, _ = _service(_strategy(side_effect=slow))
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        await svc.probe_strategy_health("aws")
        task.cancel()
        assert ticks >= 5
