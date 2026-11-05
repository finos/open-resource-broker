"""TLS settings for PostgreSQL connections through the pg8000 driver.

pg8000 has no ``sslmode`` option; it takes an ``ssl_context``. The libpq mode
names are mapped onto contexts so operators can use the familiar settings.
"""

from __future__ import annotations

import ssl
from typing import Any, Optional

# "allow" and "prefer" fall back to plain text, which pg8000 cannot do on its own.
SUPPORTED_SSLMODES = ("disable", "require", "verify-ca", "verify-full")
DEFAULT_SSLMODE = "require"


def validate_sslmode(mode: str) -> str:
    """Return the mode, or raise ValueError naming the supported ones."""
    if mode not in SUPPORTED_SSLMODES:
        raise ValueError(f"sslmode must be one of {list(SUPPORTED_SSLMODES)}")
    return mode


def pg8000_ssl_context(sslmode: str, sslrootcert: Optional[str] = None) -> Optional[ssl.SSLContext]:
    """SSL context for a libpq-style sslmode; None for ``disable``.

    - require: encrypted, certificate not verified
    - verify-ca: encrypted, certificate chain verified, host name not checked
    - verify-full: encrypted, certificate chain and host name verified
    """
    validate_sslmode(sslmode)
    if sslmode == "disable":
        return None
    if sslmode == "require":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    context = ssl.create_default_context(cafile=sslrootcert)
    if sslmode == "verify-ca":
        context.check_hostname = False
    return context


def postgres_connect_args(
    sslmode: Optional[str], sslrootcert: Optional[str] = None
) -> dict[str, Any]:
    """``connect_args`` for create_engine on PostgreSQL (empty when TLS is off)."""
    context = pg8000_ssl_context(sslmode or DEFAULT_SSLMODE, sslrootcert)
    return {"ssl_context": context} if context is not None else {}
