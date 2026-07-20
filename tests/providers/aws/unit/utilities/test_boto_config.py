"""Unit tests for boto_config utilities.

Tests verify that get_boto3_config correctly wires timeout fields and
retry settings into the botocore Config object.
"""

from __future__ import annotations

import pytest

from orb.providers.aws.utilities import boto_config


@pytest.mark.unit
class TestGetBoto3Config:
    def test_returns_config_with_default_timeout(self):
        """Without an explicit timeout the STANDARD_TIMEOUT values are used."""
        from botocore.config import Config

        cfg = boto_config.get_boto3_config()
        assert isinstance(cfg, Config)
        # STANDARD_TIMEOUT connect and read defaults must be present
        assert cfg.connect_timeout is not None  # type: ignore[attr-defined]
        assert cfg.read_timeout is not None  # type: ignore[attr-defined]

    def test_custom_timeout_values_are_applied(self):
        """Explicit TimeoutConfig values override defaults."""
        from botocore.config import Config

        from orb.infrastructure.utilities.network_utils import TimeoutConfig

        tc = TimeoutConfig(connect=7.0, read=13.0)
        cfg = boto_config.get_boto3_config(timeout=tc)
        assert isinstance(cfg, Config)
        assert cfg.connect_timeout == 7.0  # type: ignore[attr-defined]
        assert cfg.read_timeout == 13.0  # type: ignore[attr-defined]

    def test_max_retries_applied(self):
        """max_retries value appears in the retries config dict."""
        cfg = boto_config.get_boto3_config(max_retries=5)
        assert cfg.retries["max_attempts"] == 5

    def test_default_retry_mode_is_adaptive(self):
        """Default retry mode is 'adaptive'."""
        cfg = boto_config.get_boto3_config()
        assert cfg.retries["mode"] == "adaptive"

    def test_extra_kwargs_passed_through(self):
        """Extra kwargs are forwarded to botocore.Config."""
        from botocore.config import Config

        cfg = boto_config.get_boto3_config(region_name="us-west-2")
        assert isinstance(cfg, Config)

    def test_returns_none_when_botocore_unavailable(self):
        """When botocore.config cannot be imported, get_boto3_config returns None."""
        # Patch the `from botocore.config import Config` inside the function
        import importlib
        import sys

        saved = sys.modules.get("botocore.config")
        try:
            sys.modules["botocore.config"] = None  # type: ignore[assignment]
            importlib.reload(boto_config)
            result = boto_config.get_boto3_config()
            assert result is None
        finally:
            if saved is None:
                sys.modules.pop("botocore.config", None)
            else:
                sys.modules["botocore.config"] = saved
            # Reload to restore normal state
            importlib.reload(boto_config)
