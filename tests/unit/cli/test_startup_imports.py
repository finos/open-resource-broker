"""Guard the CLI startup path against eager imports of heavy optional stacks.

Each command group is run with ``--help`` in a fresh interpreter and the set of
loaded top-level modules is inspected. Command groups are discovered from the
CLI registry so new groups are covered automatically.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

# Cloud SDKs, the web stack and other optional dependencies that a command
# which only parses arguments must not load.
HEAVY_MODULES = (
    "azure",
    "boto3",
    "botocore",
    "fastapi",
    "google",
    "grpc",
    "kubernetes",
    "mcp",
    "oci",
    "reflex",
    "sqlalchemy",
    "uvicorn",
)

# Command groups that start or manage the API/UI server legitimately load the
# web stack when they run.
SERVER_GROUPS = frozenset({"server"})

_PROBE = """
import json, sys
sys.argv = ["orb", *sys.argv[1:]]
from orb.run import cli_main
try:
    cli_main()
except SystemExit:
    pass
loaded = {name.split(".")[0] for name in sys.modules}
print(json.dumps(sorted(loaded)))
"""


def _command_groups() -> list[str]:
    from orb.cli.registry import _REGISTRY, build_registry

    build_registry()
    return sorted({resource for resource, _ in _REGISTRY} - SERVER_GROUPS)


def _loaded_modules(*argv: str) -> set[str]:
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, *argv],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.stdout.strip(), proc.stderr
    return set(json.loads(proc.stdout.strip().splitlines()[-1]))


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.parametrize("argv", [("--help",), ("--version",)])
def test_top_level_commands_do_not_load_heavy_modules(argv: tuple[str, ...]) -> None:
    loaded = _loaded_modules(*argv)
    assert sorted(loaded & set(HEAVY_MODULES)) == []


@pytest.mark.unit
@pytest.mark.cli
@pytest.mark.parametrize("group", _command_groups())
def test_command_group_help_does_not_load_heavy_modules(group: str) -> None:
    loaded = _loaded_modules(group, "--help")
    assert sorted(loaded & set(HEAVY_MODULES)) == []


@pytest.mark.unit
@pytest.mark.cli
def test_help_completes_within_generous_ceiling() -> None:
    """Smoke check only: catches order-of-magnitude regressions, not tuning."""
    start = time.monotonic()
    _loaded_modules("--help")
    assert time.monotonic() - start < 10
