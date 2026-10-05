"""Non-reversible token fingerprinting for ``AuthResult``.

JWT-based authentication strategies previously stored the raw bearer token on
``AuthResult.token``, which is attached wholesale to ``request.state`` by
``AuthMiddleware``. That keeps the full credential live in server memory and
in any object graph reachable from ``request.state`` for the duration of the
request, which is more exposure than any current caller needs — nothing
downstream reads ``AuthResult.token``; identity and claims are read from
``user_id``/``user_roles``/``metadata`` instead.

:func:`fingerprint_token` gives strategies a stand-in value for ``.token``
that is useful for correlating log lines or debugging a specific request
without ever being able to reconstruct the original credential.
"""

from __future__ import annotations

import hashlib

# Long enough to make accidental collisions a non-issue for debugging/log
# correlation, short enough that it is obviously not a usable credential.
_FINGERPRINT_LENGTH = 16


def fingerprint_token(token: str) -> str:
    """Return a short, non-reversible fingerprint of *token*.

    Args:
        token: The raw bearer token (JWT) to fingerprint.

    Returns:
        The first ``_FINGERPRINT_LENGTH`` hex characters of the token's
        SHA-256 digest. Deterministic for a given token, but the original
        token cannot be recovered from the fingerprint.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:_FINGERPRINT_LENGTH]
