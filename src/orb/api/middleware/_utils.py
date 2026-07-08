"""Shared helpers for middleware — kept in a private module to avoid circular imports."""

import re
import uuid
from typing import Optional

from fastapi import Request


def get_real_client_ip(request: Request, trusted_proxies: frozenset[str]) -> Optional[str]:
    """Resolve the real client IP address, honouring trusted-proxy headers.

    X-Forwarded-For is only trusted when the direct client IP is in the
    ``trusted_proxies`` set.  When ``trusted_proxies`` is empty (the default),
    the direct connection IP is always used, preventing clients from spoofing
    their address via the X-Forwarded-For header.

    Args:
        request: The incoming Starlette/FastAPI request.
        trusted_proxies: A frozenset of IP addresses that are trusted to set
            the X-Forwarded-For header.  Pass ``frozenset()`` to always use
            the direct connection IP.

    Returns:
        The resolved client IP string, or ``None`` when the connection has no
        client information (e.g. test stubs that omit ``request.client``).
    """
    direct_ip: Optional[str] = request.client.host if request.client else None

    if direct_ip and trusted_proxies and direct_ip in trusted_proxies:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # Walk the XFF chain from RIGHT to LEFT, skipping trusted proxies.
            # The rightmost entry is appended by the closest trusted proxy and is
            # therefore the most reliable.  We stop at the first IP that is NOT in
            # trusted_proxies — that is the true client address.
            ips = [ip.strip() for ip in forwarded_for.split(",")]
            for ip in reversed(ips):
                if ip not in trusted_proxies:
                    return ip
            # All entries were trusted proxies — fall back to the direct client IP.

    return direct_ip


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f\x80-\x9f\u2028\u2029]")
_MAX_HEADER_VALUE_LENGTH = 128


def sanitize_header_value(value: str) -> str:
    """Strip ASCII control characters from a header value and enforce a length cap.

    Removes all characters in the ranges U+0000–U+001F (C0 controls, including
    CR ``\\r`` and LF ``\\n``) and U+007F (DEL).  This prevents log-injection
    attacks where a crafted header value embeds newlines that split an audit
    log entry into multiple lines with attacker-controlled fields.

    After stripping, the value is truncated to ``_MAX_HEADER_VALUE_LENGTH``
    characters (128) so unbounded user-supplied strings cannot bloat logs.

    Args:
        value: The raw header value string.

    Returns:
        The sanitized string, at most 128 characters long.
    """
    return _CONTROL_CHAR_RE.sub("", value)[:_MAX_HEADER_VALUE_LENGTH]


def get_or_generate_correlation_id(request: Request, fallback: str = "") -> str:
    """Return a sanitized X-Correlation-ID header value, generating one if absent or empty.

    If the header is present but contains only control characters (which are
    stripped), the result will be empty and a fresh UUID4 is generated as the
    fallback.

    Args:
        request: The incoming request.
        fallback: Value to use when the header is absent or becomes empty after
            sanitization.  Defaults to ``""``; when the caller passes an empty
            string a new UUID4 is generated automatically.

    Returns:
        A non-empty correlation ID string.
    """
    raw = request.headers.get("x-correlation-id", "")
    sanitized = sanitize_header_value(raw) if raw else ""
    if sanitized:
        return sanitized
    return fallback if fallback else str(uuid.uuid4())
