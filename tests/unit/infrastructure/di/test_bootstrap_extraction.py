"""Tests verifying the composition root lives in orb.bootstrap, not orb.infrastructure.di."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_bootstrap_package_exists():
    import orb.bootstrap

    assert hasattr(orb.bootstrap, "register_all_services")


@pytest.mark.unit
def test_infrastructure_di_no_longer_has_registration_modules():
    with pytest.raises(ModuleNotFoundError):
        import orb.infrastructure.di.services  # noqa: F401


@pytest.mark.unit
def test_container_factory_hook():
    from orb.bootstrap.services import register_all_services
    from orb.infrastructure.di.container import (
        get_container,
        reset_container,
        set_container_factory,
    )

    mock_fn = MagicMock()
    reset_container()
    set_container_factory(mock_fn)
    try:
        get_container()
    except Exception:
        pass
    mock_fn.assert_called_once()
    # Restore real factory explicitly (re-import is a no-op if already cached)
    set_container_factory(register_all_services)
    reset_container()


@pytest.mark.unit
def test_arch_checker_passes():
    result = subprocess.run(
        ["python", "dev-tools/quality/check_architecture.py"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[4],
    )
    assert result.returncode == 0, f"Arch checker failed:\n{result.stdout}\n{result.stderr}"


@pytest.mark.unit
def test_get_container_still_works_after_move():
    import orb.bootstrap  # ensure factory registered  # noqa: F401
    from orb.infrastructure.di.container import get_container, reset_container

    reset_container()
    container = get_container()
    assert container is not None
