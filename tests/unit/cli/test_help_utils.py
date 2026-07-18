"""Unit tests for orb.cli.help_utils.

print_getting_started_help imports console helpers lazily inside the function
body via ``from orb.cli.console import ...``.  We patch the helpers at their
canonical location in the console adapter so the patching works regardless of
when the module is first imported.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# help_utils does: from orb.cli.console import print_command, print_info
# patch at the module that is being imported-from (the re-export layer).
_INFO_PATH = "orb.cli.console.print_info"
_CMD_PATH = "orb.cli.console.print_command"


@pytest.mark.unit
class TestPrintGettingStartedHelp:
    """print_getting_started_help must invoke the console helpers in order."""

    def test_calls_print_info_and_print_command(self):
        with (
            patch(_INFO_PATH) as mock_info,
            patch(_CMD_PATH) as mock_cmd,
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
            patch(_INFO_PATH, side_effect=record),
            patch(_CMD_PATH, side_effect=record),
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
            patch(_INFO_PATH, side_effect=record),
            patch(_CMD_PATH, side_effect=record),
        ):
            from orb.cli.help_utils import print_getting_started_help

            print_getting_started_help()

        combined = "\n".join(captured_calls)
        assert "machines request" in combined

    def test_does_not_raise(self):
        with (
            patch(_INFO_PATH),
            patch(_CMD_PATH),
        ):
            from orb.cli.help_utils import print_getting_started_help

            # Must not raise under any circumstances
            print_getting_started_help()
