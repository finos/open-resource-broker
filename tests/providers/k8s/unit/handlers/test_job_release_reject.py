"""Unit tests for the Job handler's release-time subset guard.

Kubernetes Jobs cannot be scaled down safely (``parallelism`` is not
mutable post-creation under ORB's ``backoffLimit=0`` invariant).  A
subset release would silently delete every pod of the Job — including
those the caller did not ask to remove.  The handler now refuses the
request in that case.

When ``provider_data["parallelism"]`` is absent the handler resolves
parallelism from the live Job spec before deciding whether the release
is full or partial.  This keeps the selective-release guard intact for
legitimate full releases that omit parallelism in provider_data.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.k8s.exceptions.k8s_exceptions import K8sError
from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler


def _make_handler(*, live_parallelism: int = 0) -> K8sJobHandler:
    """Build a minimal K8sJobHandler stub.

    ``live_parallelism`` controls the return value of
    ``_resolve_parallelism_from_live_job``:

    * ``> 0``  — simulates a successful live read returning that value.
    * ``0``    — simulates a failed/missing live read (can't confirm full).
    """
    handler = object.__new__(K8sJobHandler)
    handler._logger = MagicMock()  # type: ignore[attr-defined]
    handler._metrics = None  # type: ignore[attr-defined]
    handler._resolve_namespace_from_provider_data = MagicMock(return_value="ns")  # type: ignore[attr-defined]
    handler._resolve_job_name_from_provider_data = MagicMock(return_value="orb-job")  # type: ignore[attr-defined]
    handler._delete_job = AsyncMock()  # type: ignore[attr-defined]
    handler._resolve_parallelism_from_live_job = AsyncMock(  # type: ignore[attr-defined]
        return_value=live_parallelism
    )
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
async def test_release_resolves_parallelism_from_live_job_when_absent() -> None:
    """Full release succeeds when parallelism is absent in provider_data but
    the live Job read resolves it to a value >= len(machine_ids).

    Regression test for the case where a return request stamps only
    ``job_name`` (no ``parallelism``) into provider_data — previously the
    handler wrongly refused with "missing parallelism".
    """
    # Simulate a parallelism-2 Job resolved from the live API.
    handler = _make_handler(live_parallelism=2)
    await handler.release_hosts(
        machine_ids=["pod-1", "pod-2"],
        provider_data={
            "request_id": "req-test",
            "namespace": "ns",
            "job_name": "orb-job",
            # 'parallelism' intentionally absent — must be resolved from live Job
        },
    )
    handler._resolve_parallelism_from_live_job.assert_awaited_once()  # type: ignore[attr-defined]
    handler._delete_job.assert_awaited_once_with("ns", "orb-job")  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_release_refuses_subset_when_parallelism_resolved_from_live_job() -> None:
    """Subset release is still refused even when parallelism is resolved via live read.

    The selective-release guard must not be weakened just because the
    parallelism came from the live Job spec rather than provider_data.
    """
    # Live read returns parallelism=5; caller only supplies 2 machine_ids.
    handler = _make_handler(live_parallelism=5)
    with pytest.raises(K8sError, match="selective release refused"):
        await handler.release_hosts(
            machine_ids=["pod-1", "pod-2"],
            provider_data={
                "request_id": "req-test",
                "namespace": "ns",
                "job_name": "orb-job",
                # 'parallelism' intentionally absent — resolved to 5 from live Job
            },
        )
    handler._resolve_parallelism_from_live_job.assert_awaited_once()  # type: ignore[attr-defined]
    handler._delete_job.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_release_refused_when_live_read_unavailable_unknown_parallelism() -> None:
    """When provider_data has no parallelism and live read also fails (returns 0),
    the release is refused rather than blindly deleting the Job.

    This covers the case where the Job is already gone or the apiserver is
    unreachable — we cannot confirm the caller has the full pod count.
    """
    # live_parallelism=0 means the live read returned nothing usable.
    handler = _make_handler(live_parallelism=0)
    with pytest.raises(K8sError, match="missing 'parallelism'"):
        await handler.release_hosts(
            machine_ids=["pod-1", "pod-2"],
            provider_data={
                "request_id": "req-test",
                "namespace": "ns",
                "job_name": "orb-job",
                # 'parallelism' absent; live read also returns 0
            },
        )
    handler._resolve_parallelism_from_live_job.assert_awaited_once()  # type: ignore[attr-defined]
    handler._delete_job.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_release_refused_when_parallelism_absent_and_live_read_fails() -> None:
    """Missing parallelism AND a failed live read is refused — cannot confirm full release.

    When provider_data carries no 'parallelism' the handler tries a live
    read_namespaced_job.  If that also returns 0 (e.g. the Job is already gone
    or the API call failed), the release must still be refused rather than
    cascade-deleting pods the caller may not have intended.
    """
    # live_parallelism=0 simulates a failed/unavailable live read.
    handler = _make_handler(live_parallelism=0)
    with pytest.raises(K8sError, match="missing 'parallelism'"):
        await handler.release_hosts(
            machine_ids=["pod-1"],
            provider_data={
                "request_id": "req-test",
                "namespace": "ns",
                "job_name": "orb-job",
                # 'parallelism' intentionally absent; live read also returns 0
            },
        )
    handler._delete_job.assert_not_called()  # type: ignore[attr-defined]
    handler._resolve_parallelism_from_live_job.assert_awaited_once()  # type: ignore[attr-defined]
