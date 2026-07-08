"""Shared provider exception hierarchy.

Import from here, not from the internal module:

::

    from orb.providers.base.exceptions import (
        ProviderError,
        ProviderConfigError,
        ProviderAuthError,
        ProviderQuotaError,
        ProviderTransientError,
        ProviderPermanentError,
    )
"""

from orb.providers.base.exceptions.provider_error import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderError,
    ProviderPermanentError,
    ProviderQuotaError,
    ProviderTransientError,
)

__all__: list[str] = [
    "ProviderError",
    "ProviderConfigError",
    "ProviderAuthError",
    "ProviderQuotaError",
    "ProviderTransientError",
    "ProviderPermanentError",
]
