"""OCI realm DNS suffixes accepted for signed launch requests.

The launch host is built from the configured region and realm suffix, so the
suffix is an allowlist rather than a free-form value. Known suffixes come from the
realm tables published in the OCI Python and Go SDKs. A realm that is not listed
here is enabled explicitly through the ``extra_realm_suffixes`` provider setting.
Operators must only add domains that Oracle owns for an OCI realm: the suffix is
used to build the host that signed requests are sent to.
"""

import re
from collections.abc import Iterable

REALM_DOMAIN_PATTERN = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")

# Extra suffixes need at least two labels and an alphabetic top-level label, so a bare
# TLD such as ``com`` or a single label can never be added.
EXTRA_REALM_SUFFIX_PATTERN = re.compile(r"^(?:[a-z0-9]+(?:-[a-z0-9]+)*\.)+[a-z]{2,}$")

KNOWN_REALM_DOMAINS: frozenset[str] = frozenset(
    {
        "oraclecloud.com",
        "oraclegovcloud.com",
        "oraclegovcloud.uk",
        "oraclecloud.eu",
        *(
            f"oraclecloud{realm}.com"
            for realm in (8, 9, 10, 14, 15, 20, 21, 23, 24, 26, 29, 35, 42, 51, 52)
        ),
    }
)


def is_valid_realm_shape(domain: object) -> bool:
    """Return True when the value is a lowercase DNS suffix with no stray characters."""
    return isinstance(domain, str) and REALM_DOMAIN_PATTERN.fullmatch(domain) is not None


def is_valid_extra_realm_suffix(domain: object) -> bool:
    """Return True when the value can be added through ``extra_realm_suffixes``."""
    return isinstance(domain, str) and EXTRA_REALM_SUFFIX_PATTERN.fullmatch(domain) is not None


def is_allowed_realm_domain(domain: object, extra_suffixes: Iterable[str] | None = None) -> bool:
    """Return True when the suffix is a known realm or an explicitly configured one."""
    if not is_valid_realm_shape(domain):
        return False
    if domain in KNOWN_REALM_DOMAINS:
        return True
    if isinstance(extra_suffixes, str):
        # A bare string is one suffix, never a sequence of single characters.
        extra_suffixes = (extra_suffixes,)
    return any(
        domain == extra and is_valid_extra_realm_suffix(extra) for extra in extra_suffixes or ()
    )
