"""Opaque keyset cursors for the machine, request and return-request lists.

A cursor names the last row of the page that was returned and carries a
fingerprint of everything that decides which rows a query returns, so a cursor
cannot be replayed against a different filter or sort.

The older ``{"offset": n}`` cursor stays accepted: it is how earlier releases
paged and how a client holding such a cursor continues. The cursors written
here carry the same ``offset`` key (the number of rows already returned), so a
release that only understands offsets keeps paging, best effort.

The cursor is not signed. It can only point at a position inside the listing the
caller is already allowed to read, and the fingerprint stops it being reused for
another query.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import hashlib
import json
from typing import Any, Optional, TypeVar

from orb.application.services.orchestration.dtos import Paginated
from orb.domain.base.exceptions import ValidationError
from orb.domain.base.pagination import Position

CURSOR_VERSION = 2

T = TypeVar("T")

_INVALID = "Invalid pagination cursor"


@dataclasses.dataclass(frozen=True)
class KeysetPaginated(Paginated[T]):
    """A page cut by keyset; ``next_position`` is where the following page starts.

    ``next_position`` is None on the last page.
    """

    next_position: Optional[Position] = None


@dataclasses.dataclass(frozen=True)
class DecodedCursor:
    """What a cursor says about where to continue.

    ``position`` is set for keyset cursors; legacy cursors only carry ``offset``.
    ``offset`` is the number of rows returned so far.
    """

    position: Optional[Position]
    offset: int


def query_fingerprint(kind: str, **inputs: Any) -> str:
    """Short hash of every input that changes which rows a listing returns.

    ``limit``, ``offset`` and ``cursor`` must not be passed: they change the
    window, not the result set. Inputs that are unset (None, empty, False) do not
    count, so an absent filter and an empty one hash the same.
    """
    normalised: list[Any] = []
    for name in sorted(inputs):
        value = inputs[name]
        if value in (None, "", [], (), False):
            continue
        if isinstance(value, (list, tuple, set)):
            value = sorted(str(v) for v in value)
        normalised.append([name, value])
    digest = hashlib.sha256(json.dumps([kind, normalised], default=str).encode()).digest()
    return base64.urlsafe_b64encode(digest[:8]).decode().rstrip("=")


def encode_page_cursor(fingerprint: str, sort: str, position: Position, offset: int) -> str:
    """Encode where the next page starts."""
    payload = {
        "v": CURSOR_VERSION,
        "s": sort,
        "f": fingerprint,
        "k": [position[0], position[1]],
        "offset": offset,
    }
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def decode_page_cursor(
    cursor: Optional[str], fingerprint: str, sort: str
) -> Optional[DecodedCursor]:
    """Decode ``cursor`` for a query with this fingerprint and sort.

    Returns None when there is no cursor. Raises ``ValidationError`` for a cursor
    that cannot be decoded, has an unknown version, or was issued for a
    different query (the message never repeats the cursor).
    """
    if not cursor:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(_pad(cursor)).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise ValidationError(_INVALID) from None
    if not isinstance(data, dict):
        raise ValidationError(_INVALID)

    if "v" not in data:
        # Legacy offset cursor
        offset = data.get("offset")
        if isinstance(offset, int) and not isinstance(offset, bool) and offset >= 0:
            return DecodedCursor(position=None, offset=offset)
        raise ValidationError(_INVALID)

    key = data.get("k")
    offset = data.get("offset", 0)
    if (
        data.get("v") != CURSOR_VERSION
        or not isinstance(key, list)
        or len(key) != 2
        or not all(isinstance(part, str) for part in key)
        or not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
    ):
        raise ValidationError(_INVALID)
    if data.get("f") != fingerprint or data.get("s") != sort:
        raise ValidationError(
            "This pagination cursor was issued for a different query. Start again without a cursor."
        )
    return DecodedCursor(position=(key[0], key[1]), offset=offset)


def _pad(cursor: str) -> bytes:
    return (cursor + "=" * (-len(cursor) % 4)).encode()


def next_cursor_for(
    result: Any,
    *,
    fingerprint: str,
    sort: str,
    offset: int,
    returned: int,
) -> tuple[bool, Optional[str]]:
    """The ``next_cursor`` for a handler result.

    Returns ``(handled, cursor)``; ``handled`` is False when the result was not
    cut by keyset and the caller must keep its offset-based cursor.
    """
    if not isinstance(result, KeysetPaginated):
        return False, None
    if result.next_position is None:
        return True, None
    return True, encode_page_cursor(fingerprint, sort, result.next_position, offset + returned)


__all__ = [
    "CURSOR_VERSION",
    "DecodedCursor",
    "KeysetPaginated",
    "decode_page_cursor",
    "encode_page_cursor",
    "next_cursor_for",
    "query_fingerprint",
]
