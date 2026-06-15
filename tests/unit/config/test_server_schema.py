"""Unit tests for server_schema configuration models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# BearerTokenAuthSubConfig — algorithm allowlist validator
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bearer_token_config_default_algorithm_is_hs256():
    """Default algorithm is HS256."""
    from orb.config.schemas.server_schema import BearerTokenAuthSubConfig

    cfg = BearerTokenAuthSubConfig(secret_key="a" * 32)
    assert cfg.algorithm == "HS256"


@pytest.mark.unit
@pytest.mark.parametrize("algo", ["HS256", "HS384", "HS512"])
def test_bearer_token_config_valid_algorithms(algo: str):
    """HS256, HS384, and HS512 are all accepted."""
    from orb.config.schemas.server_schema import BearerTokenAuthSubConfig

    cfg = BearerTokenAuthSubConfig(secret_key="a" * 32, algorithm=algo)
    assert cfg.algorithm == algo


@pytest.mark.unit
@pytest.mark.parametrize("algo", ["none", "None", "NONE"])
def test_bearer_token_config_rejects_none_algorithm(algo: str):
    """algorithm='none' (any casing) raises ValidationError with a descriptive message."""
    from orb.config.schemas.server_schema import BearerTokenAuthSubConfig

    with pytest.raises(ValidationError) as exc_info:
        BearerTokenAuthSubConfig(secret_key="a" * 32, algorithm=algo)

    errors = exc_info.value.errors()
    assert any("none" in str(e).lower() for e in errors)


@pytest.mark.unit
@pytest.mark.parametrize("algo", ["RS256", "ES256", "HS1", "unknown"])
def test_bearer_token_config_rejects_unsupported_algorithms(algo: str):
    """Non-HMAC and unknown algorithms raise ValidationError."""
    from orb.config.schemas.server_schema import BearerTokenAuthSubConfig

    with pytest.raises(ValidationError):
        BearerTokenAuthSubConfig(secret_key="a" * 32, algorithm=algo)
