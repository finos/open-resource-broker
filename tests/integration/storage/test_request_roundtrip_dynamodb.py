"""Request aggregate round-trip through the DynamoDB UnitOfWork (moto).

Exercises the real consumer path for timestamp handling: a Request is
saved via uow.requests.save(...) and read back via get_by_id(...), which
runs RequestSerializer.from_dict and calls datetime.fromisoformat on
created_at. If the storage converter pre-parses ISO strings to datetime,
that read raises "fromisoformat: argument must be str". This test fails
on that regression and passes when storage returns timestamps as strings.
"""

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.request_identifiers import RequestId
from orb.domain.request.request_types import RequestType


@pytest.mark.integration
class TestRequestRoundTripDynamoDB:
    def _make_request(self) -> Request:
        return Request(
            request_id=RequestId.generate(RequestType.ACQUIRE),
            request_type=RequestType.ACQUIRE,
            template_id="RunInstances-OnDemand",
            provider_type="aws",
            requested_count=2,
            provider_api="RunInstances",
        )

    def test_save_then_get_by_id_reads_back(self, dynamodb_uow):
        request = self._make_request()

        with dynamodb_uow as uow:
            uow.requests.save(request)

        with dynamodb_uow as uow:
            # The read path runs from_dict -> datetime.fromisoformat(created_at);
            # must not raise "fromisoformat: argument must be str".
            loaded = uow.requests.get_by_id(request.request_id)

        assert loaded is not None
        assert str(loaded.request_id) == str(request.request_id)
        assert loaded.requested_count == 2
        assert loaded.provider_api == "RunInstances"
        # created_at round-trips back to a real datetime on the aggregate.
        assert loaded.created_at is not None
