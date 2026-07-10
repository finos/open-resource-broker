from __future__ import annotations

import pytest

from orb.infrastructure.resilience.retry_decorator import retry


def test_retry_decorator_rejects_coroutine_functions() -> None:
    @retry()
    async def _async_operation() -> str:
        return "ok"

    with pytest.raises(TypeError, match="does not support coroutine functions"):
        _async_operation()
