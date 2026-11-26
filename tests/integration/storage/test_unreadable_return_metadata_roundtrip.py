"""The unreadable machine ids recorded on a failed return survive storage."""

from __future__ import annotations

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.request_identifiers import RequestId
from orb.domain.request.request_types import RequestStatus, RequestType


@pytest.mark.integration
def test_failed_return_keeps_its_unreadable_machine_ids(unit_of_work) -> None:
    request = Request(
        request_id=RequestId.generate(RequestType.RETURN),
        request_type=RequestType.RETURN,
        template_id="tpl-1",
        provider_type="k8s",
        provider_api="Pod",
        requested_count=2,
        machine_ids=["pod-a", "pod-b"],
    )
    failed = request.update_status(RequestStatus.FAILED, "could not confirm", force=True)
    failed = failed.update_metadata({"unreadable_resource_ids": ["pod-a", "pod-b"]})

    with unit_of_work as uow:
        uow.requests.save(failed)
    with unit_of_work as uow:
        loaded = uow.requests.get_by_id(failed.request_id)

    assert loaded is not None
    assert loaded.status == RequestStatus.FAILED
    assert loaded.metadata["unreadable_resource_ids"] == ["pod-a", "pod-b"]
