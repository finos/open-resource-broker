"""Fuzz configuration schema validation.

Target: orb.config.schemas.validate_config, fed with arbitrary JSON objects
(the shape a user config file takes after json.load).

Properties:
  * Only ValueError (pydantic ValidationError is a subclass) escapes from
    validate_config; any other exception type is a bug.
  * ConfigurationLoader.create_app_config only ever raises ConfigurationError.
  * A config that validates yields an AppConfig.
"""

from __future__ import annotations

import json
import sys

import _common
import atheris

_common.prepare_environment()

with atheris.instrument_imports(include=["orb"]):
    from orb.config.loader import ConfigurationLoader
    from orb.config.schemas import AppConfig, validate_config
    from orb.domain.base.exceptions import ConfigurationError


def test_one_input(data: bytes) -> None:
    try:
        config = json.loads(data)
    except (ValueError, RecursionError):
        return
    if not isinstance(config, dict):
        return

    try:
        result = validate_config(dict(config))
    except ValueError:
        result = None  # rejecting malformed configuration is the expected outcome
    assert result is None or isinstance(result, AppConfig)

    try:
        created = ConfigurationLoader.create_app_config(dict(config))
    except ConfigurationError:
        created = None
    assert created is None or isinstance(created, AppConfig)


if __name__ == "__main__":
    _common.run(atheris, test_one_input)
    sys.exit(0)
