"""Unit tests for orb.cli.help_utils.

print_getting_started_help imports console helpers lazily inside the function
body via ``from orb.cli.console import ...``.  We patch the helpers at their
canonical location in the console adapter so the patching works regardless of
when the module is first imported.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest


# help_utils does a function-local ``from orb.cli.console import ...`` which
# resolves through ``sys.modules``.  A dotted-string ``patch("orb.cli.console.x")``
# instead resolves through the ``console`` attribute on the ``orb.cli`` package,
# which can reference a stale module object if another test has re-imported the
# module.  Patch the module object that ``sys.modules`` hands out so both lookups
# agree regardless of test order.
def _console():
    return importlib.import_module("orb.cli.console")


def _patch_info(**kwargs):
    return patch.object(_console(), "print_info", **kwargs)


def _patch_cmd(**kwargs):
    return patch.object(_console(), "print_command", **kwargs)


@pytest.mark.unit
class TestPrintGettingStartedHelp:
    """print_getting_started_help must invoke the console helpers in order."""

    def test_calls_print_info_and_print_command(self):
        with (
            _patch_info() as mock_info,
            _patch_cmd() as mock_cmd,
        ):
            from orb.cli.help_utils import print_getting_started_help

            print_getting_started_help()

        # The function emits a fixed set of lines; assert the exact counts so an
        # accidental deletion (or duplication) of a help line is detected.
        assert mock_info.call_count == 5
        assert mock_cmd.call_count == 8

    def test_init_interactive_mentioned(self):
        captured_calls: list[str] = []

        def record(msg):
            captured_calls.append(msg)

        with (
            _patch_info(side_effect=record),
            _patch_cmd(side_effect=record),
        ):
            from orb.cli.help_utils import print_getting_started_help

            print_getting_started_help()

        combined = "\n".join(captured_calls)
        assert "init" in combined
        assert "templates" in combined

    def test_machines_request_mentioned(self):
        captured_calls: list[str] = []

        def record(msg):
            captured_calls.append(msg)

        with (
            _patch_info(side_effect=record),
            _patch_cmd(side_effect=record),
        ):
            from orb.cli.help_utils import print_getting_started_help

            print_getting_started_help()

        combined = "\n".join(captured_calls)
        assert "machines request" in combined

    def test_does_not_raise(self):
        with (
            _patch_info(),
            _patch_cmd(),
        ):
            from orb.cli.help_utils import print_getting_started_help

            # Must not raise under any circumstances
            print_getting_started_help()
