"""Regression tests for the k8s_legacy exception-handling decorator.

The specific handlers must apply on every call, not only the first one.
"""

from __future__ import annotations

import click
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


def test_return_value_is_propagated_on_every_call() -> None:
    """The wrapped function's value is returned on the first and later calls."""

    @handle_exceptions([(ValueError, "bad value")])
    def compute(a: int, b: int = 1) -> int:
        return a + b

    assert compute(2, b=3) == 5
    assert compute(2, b=3) == 5
    assert compute(10) == 11


def test_return_value_is_propagated_without_specific_handlers() -> None:
    """The value also comes back when the handler list is empty."""

    @handle_exceptions([])
    def compute() -> dict[str, bool]:
        return {"ok": True}

    assert compute() == {"ok": True}
    assert compute() == {"ok": True}


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


def test_usage_error_exits_with_default_code(capsys) -> None:
    """A click usage error is reported and exits with the default code."""

    @handle_exceptions([])
    def bad_usage() -> None:
        raise click.UsageError("bad flag")

    with pytest.raises(SystemExit) as exc_info:
        bad_usage()
    assert exc_info.value.code == EXIT_CODE_DEFAULT
    assert "Usage error: bad flag" in capsys.readouterr().err
