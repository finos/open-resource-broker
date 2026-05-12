"""Drift validator: ensures RequestSerializer.to_dict covers all Request fields.

If a field is added to Request but not to RequestSerializer (and not explicitly
excluded), this test will fail immediately — catching the omission before it
reaches production and causes silent data loss.
"""

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType


def _make_minimal_request() -> Request:
    """Build the smallest valid Request instance sufficient for serialization."""
    from datetime import datetime, timezone

    return Request(
        request_id=RequestId(value="req-00000000-0000-0000-0000-000000000001"),
        request_type=RequestType.ACQUIRE,
        provider_type="aws",
        template_id="tpl-coverage-001",
        requested_count=1,
        created_at=datetime(2026, 1, 15, 9, 58, 0, tzinfo=timezone.utc),
    )


@pytest.mark.unit
def test_request_serializer_covers_all_non_excluded_fields():
    """If a field is added to Request, RequestSerializer.to_dict must cover it.

    Failure modes:
    - ``missing``: field exists on Request, is not excluded, but to_dict omits it
      -> add the field to RequestSerializer.to_dict, or add it to
         Request._SERIALIZATION_EXCLUDED_FIELDS with a comment explaining why.
    - ``extra``: to_dict emits a key that is not a Request field and is not a
      known serializer-only meta key
      -> remove the stale key from RequestSerializer.to_dict.
    """
    from orb.infrastructure.storage.repositories.request_repository import RequestSerializer

    request = _make_minimal_request()
    serializer = RequestSerializer()
    serialized_keys = set(serializer.to_dict(request).keys())
    model_fields = set(Request.model_fields.keys())
    excluded = Request._SERIALIZATION_EXCLUDED_FIELDS

    # Keys emitted by the serializer that have no corresponding model field.
    # These are intentional serializer-level additions:
    #   machine_count   — legacy storage key for requested_count (backward compat)
    #   error_message   — legacy alias for status_message (backward compat)
    #   message         — legacy alias for status_message (backward compat for HF readers)
    #   timeout         — denormalised from metadata for fast querying
    #   tags            — denormalised from metadata for fast querying
    #   schema_version  — migration-support meta key
    serializer_only_keys = {
        "machine_count",
        "error_message",
        "message",
        "timeout",
        "schema_version",
    }

    missing = (model_fields - excluded) - serialized_keys
    extra = (serialized_keys - serializer_only_keys) - model_fields

    assert not missing, (
        f"RequestSerializer.to_dict is missing fields from Request: {missing}\n"
        "Either add them to RequestSerializer.to_dict or to "
        "Request._SERIALIZATION_EXCLUDED_FIELDS with a comment."
    )
    assert not extra, (
        f"RequestSerializer.to_dict emits keys not present on Request: {extra}\n"
        "Either add them to Request as fields, to serializer_only_keys with a comment, "
        "or remove them from to_dict."
    )
