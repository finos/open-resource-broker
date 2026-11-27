"""Structural snapshot of the CLI parser tree.

Pins every flag, dest, default, choice set, ordering and help string of every
command so that reorganising the parser modules cannot change what the CLI
accepts or prints.  The snapshot is built from the argparse objects, never from
rendered ``--help`` output, so it does not depend on the help formatter, the
terminal width or the Python version.

To refresh after an intentional CLI change::

    ORB_UPDATE_PARSER_SNAPSHOT=1 pytest tests/unit/cli/test_parser_snapshot.py
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import pytest

from orb.cli.args import build_parser
from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
from orb.providers.aws.cli.aws_cli_spec import AWSCLISpec
from orb.providers.azure.cli.azure_cli_spec import AzureCLISpec
from orb.providers.gcp.cli.gcp_cli_spec import GCPCLISpec
from orb.providers.k8s.cli.k8s_cli_spec import K8sCLISpec
from orb.providers.oci.cli.oci_cli_spec import OCICLISpec

_FIXTURE = Path(__file__).parent / "parser_snapshot.json"


def _plain(value: Any) -> Any:
    """Return *value* if it is JSON-stable, else its type name."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return type(value).__name__


def _describe_action(action: argparse.Action) -> list[Any]:
    """Positional fields (kept compact): class, option_strings, dest, required,
    default, const, choices, nargs, type, metavar, help."""
    type_ = getattr(action, "type", None)
    return [
        type(action).__name__,
        list(action.option_strings),
        action.dest,
        # argparse sets ``required`` on nargs="*" positionals differently across versions.
        action.required if action.option_strings else None,
        _plain(action.default),
        _plain(action.const),
        _plain(list(action.choices)) if action.choices is not None else None,
        _plain(action.nargs),
        getattr(type_, "__name__", None) if type_ is not None else None,
        _plain(action.metavar),
        action.help,
    ]


def _walk(parser: argparse.ArgumentParser, path: str, out: dict[str, Any]) -> None:
    actions = []
    children: list[tuple[str, argparse.ArgumentParser]] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            visible = {ca.dest: ca for ca in action._choices_actions}
            aliases: dict[int, list[str]] = {}
            for name, sub in action.choices.items():
                aliases.setdefault(id(sub), []).append(name)
            subs = []
            for name, sub in action.choices.items():
                shown = visible.get(name)
                subs.append(
                    {
                        "name": name,
                        "listed_in_help": shown is not None,
                        "help": shown.help if shown is not None else None,
                        "same_parser_as": aliases[id(sub)],
                    }
                )
                children.append((name, sub))
            actions.append(
                {
                    "class": type(action).__name__,
                    "dest": action.dest,
                    "required": action.required,
                    "metavar": _plain(action.metavar),
                    "help": action.help,
                    "subcommands": subs,
                }
            )
        else:
            actions.append(_describe_action(action))
    out[path or "<root>"] = {
        "description": None if not path else parser.description,
        "actions": actions,
    }
    for name, sub in children:
        _walk(sub, f"{path} {name}".strip(), out)


@pytest.fixture
def fixed_provider_specs(monkeypatch: pytest.MonkeyPatch):
    """Pin the provider flags to the in-tree providers, whatever other tests registered."""
    saved = dict(CLISpecRegistry.all())
    CLISpecRegistry.clear()
    for name, spec in {
        "aws": AWSCLISpec(),
        "azure": AzureCLISpec(),
        "gcp": GCPCLISpec(),
        "k8s": K8sCLISpec(),
        "oci": OCICLISpec(),
    }.items():
        CLISpecRegistry.register(name, spec)
    monkeypatch.setattr("orb.providers.registration.register_all_provider_cli_specs", lambda: None)
    yield
    CLISpecRegistry.clear()
    for name, spec in saved.items():
        CLISpecRegistry.register(name, spec)


def _snapshot() -> dict[str, Any]:
    parser, resource_parsers = build_parser()
    tree: dict[str, Any] = {}
    _walk(parser, "", tree)
    return {"resource_parsers": sorted(resource_parsers), "tree": tree}


def _dump(snapshot: dict[str, Any]) -> str:
    """One line per command so the fixture stays reviewable."""
    lines = ['{"resource_parsers": ' + json.dumps(snapshot["resource_parsers"]) + ', "tree": {']
    items = [f"{json.dumps(k)}: {json.dumps(v)}" for k, v in snapshot["tree"].items()]
    lines.append(",\n".join(items))
    lines.append("}}")
    return "\n".join(lines) + "\n"


@pytest.mark.usefixtures("fixed_provider_specs")
def test_parser_matches_snapshot() -> None:
    current = _snapshot()
    if os.environ.get("ORB_UPDATE_PARSER_SNAPSHOT"):
        _FIXTURE.write_text(_dump(current))
        pytest.skip("snapshot regenerated")
    expected = json.loads(_FIXTURE.read_text())
    # Round-trip through JSON so both sides use identical types.
    current = json.loads(json.dumps(current))
    assert sorted(current["tree"]) == sorted(expected["tree"])
    assert current["resource_parsers"] == expected["resource_parsers"]
    for path, node in expected["tree"].items():
        assert current["tree"][path] == node, f"parser drift under '{path}'"
