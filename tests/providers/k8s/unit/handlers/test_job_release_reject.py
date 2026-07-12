"""Unit tests for the Job handler's release-time subset guard.

Kubernetes Jobs cannot be scaled down safely (``parallelism`` is not
mutable post-creation under ORB's ``backoffLimit=0`` invariant).  A
subset release would silently delete every pod of the Job — including
those the caller did not ask to remove.  The handler now refuses the
request in that case.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.k8s.exceptions.k8s_exceptions import K8sError
from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler


def _make_handler() -> K8sJobHandler:
    handler = object.__new__(K8sJobHandler)
    handler._logger = MagicMock()  # type: ignore[attr-defined]
    handler._metrics = None  # type: ignore[attr-defined]
    handler._resolve_namespace_from_provider_data = MagicMock(return_value="ns")  # type: ignore[attr-defined]
    handler._resolve_job_name_from_provider_data = MagicMock(return_value="orb-job")  # type: ignore[attr-defined]
    handler._delete_job = AsyncMock()  # type: ignore[attr-defined]
    return handler


@pytest.mark.asyncio
async def test_release_rejected_when_subset_of_parallelism() -> None:
    handler = _make_handler()
    with pytest.raises(K8sError, match="Job selective release refused"):
        await handler.release_hosts(
            machine_ids=["pod-1", "pod-2"],
            provider_data={
                "request_id": "req-test",
                "namespace": "ns",
                "job_name": "orb-job",
                "parallelism": 5,
            },
        )
    handler._delete_job.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_release_accepted_when_full_parallelism() -> None:
    handler = _make_handler()
    await handler.release_hosts(
        machine_ids=["pod-1", "pod-2", "pod-3"],
        provider_data={
            "request_id": "req-test",
            "namespace": "ns",
            "job_name": "orb-job",
            "parallelism": 3,
        },
    )
    handler._delete_job.assert_awaited_once_with("ns", "orb-job")  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_release_noop_when_empty_machine_ids() -> None:
    handler = _make_handler()
    await handler.release_hosts(
        machine_ids=[],
        provider_data={
            "request_id": "req-test",
            "namespace": "ns",
            "job_name": "orb-job",
            "parallelism": 3,
        },
    )
    handler._delete_job.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_release_refused_when_parallelism_absent_from_provider_data() -> None:
    """Missing parallelism in provider_data is refused — cannot confirm full release.

    A Job release without knowing parallelism would cascade-delete all pods even
    if machine_ids only names a subset.  Callers must ensure provider_data carries
    'parallelism' (written by acquire_hosts).
    """
    handler = _make_handler()
    with pytest.raises(K8sError, match="missing 'parallelism'"):
        await handler.release_hosts(
            machine_ids=["pod-1"],
            provider_data={
                "request_id": "req-test",
                "namespace": "ns",
                "job_name": "orb-job",
                # 'parallelism' intentionally absent
            },
        )
    handler._delete_job.assert_not_called()  # type: ignore[attr-defined]
