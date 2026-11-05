"""Guard: a wait loop must never poll with a non-syncing read.

A lightweight ``SyncAndGetRequestQuery`` skips the provider sync, so polling it
in a loop cannot observe a request settling and the wait runs to its timeout.
"""

import ast
from pathlib import Path

import pytest

import orb.application as application_package

pytestmark = pytest.mark.unit

_QUERY_NAME = "SyncAndGetRequestQuery"


def _is_lightweight_request_query(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None)) == _QUERY_NAME
        and any(
            kw.arg == "lightweight"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        )
    )


def lightweight_polls_in(source: str) -> list[int]:
    """Line numbers of lightweight request queries issued inside a loop."""
    lines: set[int] = set()
    for loop in ast.walk(ast.parse(source)):
        if isinstance(loop, (ast.While, ast.For, ast.AsyncFor)):
            lines.update(n.lineno for n in ast.walk(loop) if _is_lightweight_request_query(n))
    return sorted(lines)


def test_detector_flags_a_lightweight_poll():
    source = (
        "async def wait(bus):\n"
        "    while True:\n"
        "        await bus.execute(SyncAndGetRequestQuery(request_id='r', lightweight=True))\n"
    )
    assert lightweight_polls_in(source) == [3]


def test_detector_ignores_syncing_polls_and_single_reads():
    source = (
        "async def wait(bus):\n"
        "    while True:\n"
        "        await bus.execute(SyncAndGetRequestQuery(request_id='r', lightweight=False))\n"
        "async def once(bus):\n"
        "    await bus.execute(SyncAndGetRequestQuery(request_id='r', lightweight=True))\n"
    )
    assert lightweight_polls_in(source) == []


def test_no_application_wait_loop_polls_without_a_provider_sync():
    root = Path(application_package.__file__).parent
    offenders = [
        f"{path.relative_to(root.parent)}:{line}"
        for path in sorted(root.rglob("*.py"))
        for line in lightweight_polls_in(path.read_text())
    ]
    assert not offenders, f"wait loops polling with lightweight=True: {offenders}"
