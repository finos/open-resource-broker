"""Every registered CLI command answers ``--help`` from the installed package.

The command list is read from the installed package's registry. Each command is
either exercised by the functional flows (``FUNCTIONAL``), has only its help
checked (``HELP_ONLY``) or is not a CLI subcommand at all (``REGISTRY_ONLY``), each
with a reason; a command in neither table, or a table
entry for a command that no longer exists, fails the meta-test.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.package_smoke.conftest import Smoke

Command = tuple[str, str]

FUNCTIONAL: dict[Command, str] = {
    ("init", "*"): "test_cli_flow: runs against moto",
    ("templates", "generate"): "test_cli_flow: runs against moto",
    ("templates", "list"): "test_cli_flow: runs against moto",
    ("machines", "request"): "test_cli_flow: runs against moto",
    ("requests", "status"): "test_cli_flow: runs against moto",
    ("machines", "return"): "test_cli_flow: runs against moto",
    ("server", "start"): "test_server_ui: the server is started and served",
}


def _group(resource: str, reason: str, *actions: str) -> dict[Command, str]:
    return {(resource, action): reason for action in actions}


_READ_ONLY = "read-only inspection; the flows cover the paths that write"
HELP_ONLY: dict[Command, str] = {
    **_group(
        "config",
        _READ_ONLY,
        "get",
        "reload",
        "set",
        "show",
        "validate",
    ),
    **_group(
        "infrastructure",
        "needs account discovery APIs moto does not serve",
        "discover",
        "show",
        "validate",
    ),
    **_group(
        "machines",
        "machine lifecycle beyond request and return needs cloud behaviour moto lacks",
        "list",
        "show",
        "start",
        "status",
        "stop",
        "terminate",
    ),
    **_group(
        "mcp", "long-running stdio server; its protocol has its own tests", "serve", "validate"
    ),
    **_group(
        "providers",
        "provider administration; configuration changes are covered by unit tests",
        "add",
        "exec",
        "get",
        "get-default",
        "health",
        "list",
        "metrics",
        "remove",
        "select",
        "set-default",
        "show",
        "update",
    ),
    **_group(
        "requests",
        "request inspection and control beyond status; covered by unit tests",
        "cancel",
        "confirm-terminated",
        "list",
        "list-returns",
        "show",
        "watch",
    ),
    **_group("scheduler", _READ_ONLY, "list", "show", "validate"),
    **_group(
        "server",
        "daemon control; the smoke starts the server in the foreground and stops it by PID",
        "logs",
        "reload",
        "restart",
        "status",
        "stop",
        "ui-export",
    ),
    **_group(
        "storage",
        "storage backends other than the local JSON store are not configured",
        "health",
        "list",
        "metrics",
        "migrate",
        "show",
        "test",
        "validate",
    ),
    **_group("system", _READ_ONLY, "health", "metrics", "reload", "status"),
    **_group(
        "templates",
        "template editing; covered by unit tests",
        "create",
        "delete",
        "refresh",
        "show",
        "update",
        "validate",
    ),
}


# Registered for the REST and MCP interfaces; the CLI parser has no subcommand for them.
_BULK = "bulk lookup served by the REST and MCP interfaces; the CLI has no subcommand for it"
REGISTRY_ONLY: dict[Command, str] = {
    ("machines", "get-bulk"): _BULK,
    ("requests", "get-bulk"): _BULK,
    ("templates", "get-bulk"): _BULK,
}


@pytest.fixture(scope="module")
def registry(smoke: Smoke) -> set[Command]:
    out = smoke.run_python(str(smoke.helpers / "introspect.py"), "commands")
    return {(resource, action) for resource, action in json.loads(out.stdout)}


def test_the_registry_is_not_empty(registry):
    assert len(registry) >= 20, sorted(registry)


def test_every_command_is_classified(registry):
    tables = (FUNCTIONAL, HELP_ONLY, REGISTRY_ONLY)
    classified = set().union(*tables)
    assert sum(len(t) for t in tables) == len(classified), "a command is in more than one table"
    assert not registry - classified, (
        f"add to FUNCTIONAL or HELP_ONLY: {sorted(registry - classified)}"
    )
    assert not classified - registry, f"no longer registered: {sorted(classified - registry)}"


def _help_command(command: Command) -> list[str]:
    resource, action = command
    return [resource, "--help"] if action == "*" else [resource, action, "--help"]


def test_help_works_for_every_help_only_command(smoke: Smoke, registry):
    def check(command: Command) -> str | None:
        done = smoke.run(*_help_command(command), timeout=60, ok=False)
        if done.returncode != 0:
            return f"{command}: exit {done.returncode}\n{done.stderr}"
        if "Traceback" in done.stdout + done.stderr:
            return f"{command}: traceback\n{done.stderr}"
        if not done.stdout.strip():
            return f"{command}: help printed nothing"
        return None

    commands = sorted(set(HELP_ONLY) & registry)
    with ThreadPoolExecutor(max_workers=4) as pool:
        failures = [message for message in pool.map(check, commands) if message]
    assert not failures, "\n".join(failures)
