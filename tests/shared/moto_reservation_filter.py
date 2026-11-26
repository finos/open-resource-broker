"""Emulate the EC2 ``reservation-id`` filter that moto does not implement.

moto rejects ``DescribeInstances`` calls filtered on ``reservation-id``. Real
AWS supports the filter and the production code relies on it, so tests answer
it client-side instead of the code carrying a mock-only fallback.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from botocore.client import BaseClient

_FILTER_NAME = "reservation-id"


def _wanted_reservations(filters: list[dict[str, Any]]) -> set[str]:
    return {v for f in filters if f.get("Name") == _FILTER_NAME for v in f.get("Values", [])}


@contextmanager
def emulate_reservation_id_filter() -> Iterator[None]:
    """Answer ``DescribeInstances`` ``reservation-id`` filters on the client side.

    The call is sent to moto without that filter, every result page is read,
    and only the requested reservations are returned.
    """
    original = BaseClient._make_api_call  # type: ignore[attr-defined]

    def patched(self: Any, operation_name: str, api_params: dict[str, Any]) -> Any:
        filters = api_params.get("Filters") or []
        wanted = _wanted_reservations(filters) if operation_name == "DescribeInstances" else set()
        if not wanted:
            return original(self, operation_name, api_params)

        params = {k: v for k, v in api_params.items() if k not in ("Filters", "NextToken")}
        remaining = [f for f in filters if f.get("Name") != _FILTER_NAME]
        if remaining:
            params["Filters"] = remaining

        reservations: list[dict[str, Any]] = []
        page = dict(params)
        while True:
            response = original(self, operation_name, page)
            reservations.extend(
                r for r in response.get("Reservations", []) if r.get("ReservationId") in wanted
            )
            token = response.get("NextToken")
            if not token:
                break
            page = {**params, "NextToken": token}

        response["Reservations"] = reservations
        response.pop("NextToken", None)
        return response

    BaseClient._make_api_call = patched  # type: ignore[attr-defined]
    try:
        yield
    finally:
        BaseClient._make_api_call = original  # type: ignore[attr-defined]
