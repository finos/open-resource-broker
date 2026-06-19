"""Guard against eager imports in orb.providers (task 1721).

Importing orb.providers must not trigger loading of the provider factory or
registry, which pull in heavy AWS dependencies at import time.

The eager-load check runs in a subprocess so the test never has to mutate the
parent process's ``sys.modules`` — earlier attempts at evicting and restoring
modules left the parent ``orb.providers`` package without its submodule
attributes (e.g. ``orb.providers.azure``), which broke unrelated downstream
tests that rely on ``monkeypatch.setattr`` walking dotted import paths.
"""

import subprocess
import sys


def _run_isolated_check(module_to_check: str) -> bool:
    """Spawn a clean Python that imports orb.providers and reports whether
    ``module_to_check`` got loaded as a side effect.

    Returns True if the module was loaded (i.e. eager-import violation),
    False otherwise.
    """
    script = (
        "import sys\n"
        "import orb.providers  # noqa\n"
        f"print('LOADED' if {module_to_check!r} in sys.modules else 'NOT_LOADED')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip() == "LOADED"


def test_providers_init_does_not_eagerly_load_factory():
    assert not _run_isolated_check("orb.providers.factory"), (
        "orb.providers.__init__ must not eagerly import orb.providers.factory"
    )


def test_providers_init_does_not_eagerly_load_registry():
    assert not _run_isolated_check("orb.providers.registry.provider_registry"), (
        "orb.providers.__init__ must not eagerly import orb.providers.registry.provider_registry"
    )
