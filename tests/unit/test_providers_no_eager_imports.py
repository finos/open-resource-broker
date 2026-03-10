"""Guard against eager imports in orb.providers (task 1721).

Importing orb.providers must not trigger loading of the provider factory or
registry, which pull in heavy AWS dependencies at import time.
"""

import sys
from contextlib import contextmanager


@contextmanager
def _isolated_providers_import():
    """Temporarily evict orb.providers.* from sys.modules, then restore on exit.

    Restoring the original module objects prevents downstream tests from seeing
    a second (re-imported) copy of the same module, which would break isinstance
    checks on exception classes defined in those modules.
    """
    saved = {k: v for k, v in sys.modules.items() if k.startswith("orb.providers")}
    for key in saved:
        del sys.modules[key]
    try:
        yield
    finally:
        # Remove any modules loaded during the isolated import
        for key in list(sys.modules):
            if key.startswith("orb.providers"):
                del sys.modules[key]
        # Restore the original module objects
        sys.modules.update(saved)


def test_providers_init_does_not_eagerly_load_factory():
    with _isolated_providers_import():
        import orb.providers  # noqa: F401  # type: ignore[reportUnusedImport]

        assert "orb.providers.factory" not in sys.modules, (
            "orb.providers.__init__ must not eagerly import orb.providers.factory"
        )


def test_providers_init_does_not_eagerly_load_registry():
    with _isolated_providers_import():
        import orb.providers  # noqa: F401  # type: ignore[reportUnusedImport]

        assert "orb.providers.registry.provider_registry" not in sys.modules, (
            "orb.providers.__init__ must not eagerly import orb.providers.registry.provider_registry"
        )
