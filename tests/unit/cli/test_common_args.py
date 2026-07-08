"""Tests for the shared CLI argument helpers in orb.cli.parsers.common_args.

Covers:
  - add_provider_type_arg produces a parser that accepts each registered type
  - add_provider_type_arg rejects unknown values when choices are populated
  - choices reflect registry contents (dynamic registration)
  - regression guard: build_parser() raises no argparse conflict error
"""

from __future__ import annotations

import argparse
import sys
from unittest.mock import patch

import pytest

from orb.cli.parsers.common_args import _registered_provider_types, add_provider_type_arg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser()


def _parse(parser: argparse.ArgumentParser, args: list[str]) -> argparse.Namespace:
    return parser.parse_args(args)


# ---------------------------------------------------------------------------
# add_provider_type_arg unit tests
# ---------------------------------------------------------------------------


class TestAddProviderTypeArg:
    def test_adds_provider_type_argument(self):
        parser = _fresh_parser()
        add_provider_type_arg(parser)
        all_opts = [
            opt for action in parser._actions for opt in getattr(action, "option_strings", [])
        ]
        assert "--provider-type" in all_opts

    def test_dest_is_provider_type(self):
        parser = _fresh_parser()
        add_provider_type_arg(parser)
        action = next(
            a for a in parser._actions if "--provider-type" in getattr(a, "option_strings", [])
        )
        assert action.dest == "provider_type"

    def test_default_is_none_by_default(self):
        parser = _fresh_parser()
        add_provider_type_arg(parser)
        ns = _parse(parser, [])
        assert ns.provider_type is None

    def test_custom_default_is_honoured(self):
        parser = _fresh_parser()
        add_provider_type_arg(parser, default="aws")
        ns = _parse(parser, [])
        assert ns.provider_type == "aws"

    def test_accepts_registered_provider_types(self):
        """Every type that the registry knows about must be a valid choice."""
        known_types = _registered_provider_types()
        if not known_types:
            pytest.skip("No provider types registered; skipping choices acceptance test")

        parser = _fresh_parser()
        add_provider_type_arg(parser)

        for ptype in known_types:
            ns = _parse(parser, ["--provider-type", ptype])
            assert ns.provider_type == ptype

    def test_rejects_unknown_value_when_choices_populated(self):
        """When the registry is populated, an unknown type must be rejected."""
        known_types = _registered_provider_types()
        if not known_types:
            pytest.skip("No provider types registered; choices validation not active")

        parser = _fresh_parser()
        add_provider_type_arg(parser)

        with pytest.raises(SystemExit) as exc_info:
            _parse(parser, ["--provider-type", "definitely-not-a-provider"])
        assert exc_info.value.code == 2

    def test_choices_match_registry_contents(self):
        """The choices set on the action must mirror the registry exactly."""
        known_types = _registered_provider_types()
        if not known_types:
            pytest.skip("No provider types registered; choices not set")

        parser = _fresh_parser()
        add_provider_type_arg(parser)

        action = next(
            a for a in parser._actions if "--provider-type" in getattr(a, "option_strings", [])
        )
        assert action.choices is not None
        assert set(action.choices) == set(known_types)

    def test_new_provider_reflected_when_registry_is_expanded(self):
        """Simulates a new provider type being registered before build time."""
        from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry

        original_specs = dict(CLISpecRegistry._specs)
        try:
            # Install a fake spec so the registry returns an extra type.
            fake_spec = object()
            CLISpecRegistry._specs["fake-provider"] = fake_spec  # type: ignore[assignment]

            parser = _fresh_parser()
            add_provider_type_arg(parser)

            action = next(
                a for a in parser._actions if "--provider-type" in getattr(a, "option_strings", [])
            )
            assert action.choices is not None
            assert "fake-provider" in action.choices
        finally:
            CLISpecRegistry._specs = original_specs

    def test_no_choices_without_registry_entries(self):
        """When the registry is empty, choices is None (open-ended)."""
        from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry

        original_specs = dict(CLISpecRegistry._specs)
        try:
            CLISpecRegistry._specs = {}

            parser = _fresh_parser()
            add_provider_type_arg(parser)

            action = next(
                a for a in parser._actions if "--provider-type" in getattr(a, "option_strings", [])
            )
            assert action.choices is None
        finally:
            CLISpecRegistry._specs = original_specs

    def test_extra_help_is_appended(self):
        parser = _fresh_parser()
        add_provider_type_arg(parser, extra_help="Some extra info.")
        action = next(
            a for a in parser._actions if "--provider-type" in getattr(a, "option_strings", [])
        )
        assert action.help is not None
        assert "Some extra info." in action.help

    def test_required_flag_accepted(self):
        """required=True must propagate to the argparse action."""
        parser = _fresh_parser()
        add_provider_type_arg(parser, required=True)
        action = next(
            a for a in parser._actions if "--provider-type" in getattr(a, "option_strings", [])
        )
        assert action.required is True


# ---------------------------------------------------------------------------
# Regression guard: build_parser() must raise no argparse conflict
# ---------------------------------------------------------------------------


class TestBuildParserNoConflict:
    """Guard against the argparse conflict error that caused 104 test failures.

    build_parser() calls add_global_arguments() on every leaf subparser and
    also attaches --provider-type on init.  If the same parser ever receives
    two registrations of the same flag, argparse raises ValueError immediately.
    This test ensures that never happens.
    """

    def test_build_parser_raises_no_argparse_conflict(self):
        """build_parser() must complete without raising ValueError or SystemExit."""
        from orb.cli.args import build_parser

        try:
            parser, resource_parsers = build_parser()
        except (ValueError, SystemExit) as exc:
            pytest.fail(
                f"build_parser() raised {type(exc).__name__}: {exc}. "
                "This usually means --provider-type (or another flag) was registered "
                "twice on the same parser."
            )

        assert isinstance(parser, argparse.ArgumentParser)
        assert isinstance(resource_parsers, dict)

    def test_build_parser_can_be_called_twice_without_error(self):
        """Calling build_parser() multiple times (e.g. in reload scenarios) is safe."""
        from orb.cli.args import build_parser

        build_parser()
        # Second call must also succeed — the registry is a module-level dict
        # so calling build_parser again should not accumulate duplicate registrations.
        try:
            build_parser()
        except (ValueError, SystemExit) as exc:
            pytest.fail(f"Second call to build_parser() raised {type(exc).__name__}: {exc}")

    def test_init_provider_type_default_is_aws(self):
        """orb init must default --provider-type to 'aws' for backward compatibility."""
        with patch.object(sys, "argv", ["orb", "init"]):
            from orb.cli.args import parse_args

            ns, _ = parse_args()

        assert ns.provider_type == "aws"

    def test_machines_list_provider_type_default_is_none(self):
        """For non-init subcommands the default must be None (no filtering)."""
        with patch.object(sys, "argv", ["orb", "machines", "list"]):
            from orb.cli.args import parse_args

            ns, _ = parse_args()

        assert ns.provider_type is None

    def test_machines_list_accepts_provider_type_value(self):
        with patch.object(sys, "argv", ["orb", "machines", "list", "--provider-type", "aws"]):
            from orb.cli.args import parse_args

            ns, _ = parse_args()

        assert ns.provider_type == "aws"

    def test_templates_list_accepts_provider_type_value(self):
        with patch.object(sys, "argv", ["orb", "templates", "list", "--provider-type", "aws"]):
            from orb.cli.args import parse_args

            ns, _ = parse_args()

        assert ns.provider_type == "aws"

    def test_requests_list_accepts_provider_type_value(self):
        with patch.object(sys, "argv", ["orb", "requests", "list", "--provider-type", "aws"]):
            from orb.cli.args import parse_args

            ns, _ = parse_args()

        assert ns.provider_type == "aws"


# ---------------------------------------------------------------------------
# _registered_provider_types helper
# ---------------------------------------------------------------------------


class TestRegisteredProviderTypes:
    def test_returns_list(self):
        result = _registered_provider_types()
        assert isinstance(result, list)

    def test_is_sorted(self):
        result = _registered_provider_types()
        assert result == sorted(result)

    def test_tolerates_empty_registry(self):
        from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry

        original = dict(CLISpecRegistry._specs)
        try:
            CLISpecRegistry._specs = {}
            result = _registered_provider_types()
            assert result == []
        finally:
            CLISpecRegistry._specs = original
