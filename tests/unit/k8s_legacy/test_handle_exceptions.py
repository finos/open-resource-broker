"""Regression tests for the k8s_legacy exception-handling decorator.

See https://github.com/finos/open-resource-broker/issues/670: the specific
handlers must apply on every call, not only the first one.
"""

from __future__ import annotations

import pytest

from orb.k8s_legacy import EXIT_CODE_DEFAULT, handle_exceptions


def _record_handler(seen: list[str]):
    """Return a callable handler that records the exception type and echoes it."""

    def handler(err: Exception) -> str:
        seen.append(type(err).__name__)
        return f"handled {type(err).__name__}"

    return handler


def test_handlers_apply_on_every_call(capsys) -> None:
    """The same specific handler applies to every call, not just the first."""
    seen: list[str] = []

    @handle_exceptions([(ValueError, _record_handler(seen))])
    def boom() -> None:
        raise ValueError("boom")

    for _ in range(2):
        with pytest.raises(SystemExit) as exc_info:
            boom()
        assert exc_info.value.code == EXIT_CODE_DEFAULT

    assert seen == ["ValueError", "ValueError"]
    assert capsys.readouterr().err.count("handled ValueError") == 2


def test_handlers_are_not_consumed_across_calls(capsys) -> None:
    """Handlers at every position still apply on later calls."""
    seen: list[str] = []

    @handle_exceptions(
        [
            (KeyError, _record_handler(seen)),
            (ValueError, _record_handler(seen)),
        ]
    )
    def boom(kind: str) -> None:
        if kind == "key":
            raise KeyError("missing")
        raise ValueError("boom")

    for kind in ("key", "value", "key"):
        with pytest.raises(SystemExit) as exc_info:
            boom(kind)
        assert exc_info.value.code == EXIT_CODE_DEFAULT

    assert seen == ["KeyError", "ValueError", "KeyError"]
    err = capsys.readouterr().err
    assert err.count("handled KeyError") == 2
    assert err.count("handled ValueError") == 1


def test_unhandled_exception_escapes_to_fallback(capsys) -> None:
    """Exceptions not in the list still reach the generic fallback."""
    seen: list[str] = []

    @handle_exceptions([(ValueError, _record_handler(seen))])
    def boom() -> None:
        raise RuntimeError("unhandled")

    with pytest.raises(SystemExit) as exc_info:
        boom()
    assert exc_info.value.code == EXIT_CODE_DEFAULT
    assert seen == []
    # The generic fallback names the error and writes a traceback temp file.
    assert "Error: unhandled" in capsys.readouterr().err
