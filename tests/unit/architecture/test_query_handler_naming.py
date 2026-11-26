"""Query handlers that write while answering must carry the ``SyncAnd`` prefix."""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture.conftest import SRC_ORB, collect_python_files

_PREFIX = "SyncAnd"


def _is_query_handler(cls: ast.ClassDef) -> bool:
    for dec in cls.decorator_list:
        func = dec.func if isinstance(dec, ast.Call) else dec
        if (isinstance(func, ast.Name) and func.id == "query_handler") or (
            isinstance(func, ast.Attribute) and func.attr == "query_handler"
        ):
            return True
    return False


def _calls_sync_service_writer(cls: ast.ClassDef) -> bool:
    """True when the class calls ``<...sync...>.sync_*`` or ``<...sync...>.populate_*``."""
    for node in ast.walk(cls):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        method = node.func.attr
        if not method.startswith(("sync_", "populate_")):
            continue
        receiver = node.func.value
        name = receiver.attr if isinstance(receiver, ast.Attribute) else getattr(receiver, "id", "")
        if "sync" in name.lower():
            return True
    return False


def _unprefixed_writers(source: str) -> list[str]:
    return [
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef)
        and _is_query_handler(node)
        and _calls_sync_service_writer(node)
        and not node.name.startswith(_PREFIX)
    ]


@pytest.mark.unit
@pytest.mark.architecture
def test_sync_writing_query_handlers_are_prefixed() -> None:
    offenders = {
        f.relative_to(SRC_ORB).as_posix(): names
        for f in collect_python_files(SRC_ORB / "application")
        if (names := _unprefixed_writers(f.read_text(encoding="utf-8")))
    }
    assert not offenders, f"query handlers that sync without the {_PREFIX} prefix: {offenders}"


@pytest.mark.unit
@pytest.mark.architecture
def test_list_machines_query_is_served_by_a_prefixed_handler() -> None:
    import orb.application.queries.machine_query_handlers  # noqa: F401  (registers the handlers)
    from orb.application.decorators import get_query_handler_for_type
    from orb.application.dto.queries import ListMachinesQuery

    assert get_query_handler_for_type(ListMachinesQuery).__name__.startswith(_PREFIX)


@pytest.mark.unit
@pytest.mark.architecture
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "@query_handler(Q)\nclass ListXHandler:\n    async def run(self):\n"
            "        await self._machine_sync_service.sync_machines_with_provider()\n",
            ["ListXHandler"],
        ),
        (
            "@query_handler(Q)\nclass GetXHandler:\n    async def run(self):\n"
            "        await self._machine_sync_service.populate_missing_machine_ids()\n",
            ["GetXHandler"],
        ),
        (
            "@query_handler(Q)\nclass SyncAndListXHandler:\n    async def run(self):\n"
            "        await self._machine_sync_service.sync_machines_with_provider()\n",
            [],
        ),
        (
            "@query_handler(Q)\nclass ListXHandler:\n    async def run(self):\n"
            "        await self._machine_sync_service.fetch_provider_machines()\n",
            [],
        ),
        (
            "class Plain:\n    async def run(self):\n"
            "        await self._machine_sync_service.sync_machines_with_provider()\n",
            [],
        ),
    ],
)
def test_naming_predicate(source: str, expected: list[str]) -> None:
    assert _unprefixed_writers(source) == expected
