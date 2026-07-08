"""Port-layer exceptions.

Exceptions that cross the application/infrastructure boundary live here so
application code can catch them without importing infrastructure symbols.
Infrastructure implementations raise these; application handlers catch them.
"""

from __future__ import annotations


class RepositoryQueryError(RuntimeError):
    """Raised when a repository query fails due to a database error.

    Inherits from ``RuntimeError`` so it propagates through the application
    layer without being silently swallowed, while remaining distinct from
    infrastructure-owned ``StorageError`` hierarchies for callers that want to
    handle query failures separately (e.g. the dashboard summary endpoint,
    which degrades gracefully instead of returning a 500).
    """
