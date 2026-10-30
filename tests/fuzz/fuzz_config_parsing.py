"""Fuzz the pure parsing helpers behind configuration loading.

Targets:
  * orb.infrastructure.utilities.json_utils.safe_json_loads
  * ConfigurationLoader._convert_value (ORB_* environment value coercion)
  * ConfigurationLoader._merge_config / _deep_copy (layered config merge)

Properties:
  * safe_json_loads(raise_on_error=True) raises only JSONParseError and never
    raises when raise_on_error is False.
  * _convert_value never raises and returns a JSON-compatible value.
  * Merging keeps every key of the update, and _deep_copy preserves content.
"""

from __future__ import annotations

import json
import sys

import _common
import atheris

_common.prepare_environment()

with atheris.instrument_imports(include=["orb"]):
    from orb.config.loader import ConfigurationLoader
    from orb.infrastructure.utilities.json_utils import JSONParseError, safe_json_loads

_CONVERTED_TYPES = (bool, int, float, str, dict, list)


def _as_dict(raw: bytes) -> dict | None:
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    op = fdp.ConsumeIntInRange(0, 2)

    if op == 0:
        raw = fdp.ConsumeBytes(fdp.remaining_bytes())
        safe_json_loads(raw, default=None)
        try:
            strict = safe_json_loads(raw, raise_on_error=True)
        except JSONParseError:
            strict = None
        assert strict is None or repr(strict) == repr(safe_json_loads(raw, default=None))

    elif op == 1:
        text = fdp.ConsumeBytes(fdp.remaining_bytes()).decode("utf-8", errors="replace")
        result = ConfigurationLoader._convert_value(text)
        assert isinstance(result, _CONVERTED_TYPES), type(result)
        if text.lower() in ("true", "false"):
            assert result is (text.lower() == "true")

    else:
        raw = fdp.ConsumeBytes(fdp.remaining_bytes())
        half = len(raw) // 2
        base, update = _as_dict(raw[:half]), _as_dict(raw[half:])
        if base is None or update is None:
            return
        snapshot = json.dumps(base, sort_keys=True)
        copied = ConfigurationLoader._deep_copy(base)
        assert json.dumps(copied, sort_keys=True) == snapshot
        ConfigurationLoader._merge_config(copied, update)
        assert set(update) <= set(copied)


if __name__ == "__main__":
    _common.run(atheris, test_one_input)
    sys.exit(0)
