"""Tests for health check path resolution via ORB_ROOT_DIR."""

import os
from pathlib import Path
from unittest.mock import patch


def test_orb_root_dir_flows_through_to_health_check(monkeypatch):
    """ORB_ROOT_DIR env var should produce root/work/health as health location."""
    from orb.config.platform_dirs import get_health_location

    # Remove any ambient ORB_WORK_DIR / ORB_HEALTH_DIR that a previous test
    # (e.g. one that drives CLI main() through its full setup_environment() path)
    # may have left via os.environ.setdefault.  Without this guard the
    # per-dir overrides would short-circuit the ORB_ROOT_DIR derivation path.
    monkeypatch.delenv("ORB_WORK_DIR", raising=False)
    monkeypatch.delenv("ORB_HEALTH_DIR", raising=False)

    with patch.dict(os.environ, {"ORB_ROOT_DIR": "/myroot"}, clear=False):
        result = get_health_location()
    assert result == Path("/myroot/work/health")


def test_health_location_fallback_uses_work_location():
    """Without ORB_ROOT_DIR, health location falls back to get_work_location()/health."""
    from orb.config.platform_dirs import get_health_location

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("orb.config.platform_dirs.get_work_location") as mock_work,
    ):
        mock_work.return_value = Path("/base/work")
        result = get_health_location()

    assert result == Path("/base/work/health")
