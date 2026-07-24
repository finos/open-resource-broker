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

        original_specs = dict(CLISpecRegistry._store)
        try:
            # Install a fake spec so the registry returns an extra type.
            fake_spec = object()
            CLISpecRegistry._store["fake-provider"] = fake_spec  # type: ignore[assignment]

            parser = _fresh_parser()
            add_provider_type_arg(parser)

            action = next(
                a for a in parser._actions if "--provider-type" in getattr(a, "option_strings", [])
            )
            assert action.choices is not None
            assert "fake-provider" in action.choices
        finally:
            CLISpecRegistry._store = original_specs

    def test_no_choices_without_registry_entries(self):
        """When the registry is empty, choices is None (open-ended)."""
        from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry

        original_specs = dict(CLISpecRegistry._store)
        try:
            CLISpecRegistry._store = {}

            parser = _fresh_parser()
            add_provider_type_arg(parser)

            action = next(
                a for a in parser._actions if "--provider-type" in getattr(a, "option_strings", [])
            )
            assert action.choices is None
        finally:
            CLISpecRegistry._store = original_specs

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

    build_parser() composes intent-grouped parent parsers (common/list/write/
    provider-scope/hf-compat) onto every leaf subparser via parents=[...] and
    also attaches --provider-type on init.  If a parser ever composes two
    parents that both declare the same option string, argparse raises
    ArgumentError at build time.  This test ensures that never happens.
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
# Regression guard: --provider-type is defined ONCE and survives a second
# provider registering
# ---------------------------------------------------------------------------


def _leaf_subparser(
    parser: argparse.ArgumentParser, resource: str, action: str
) -> argparse.ArgumentParser:
    """Return the leaf sub-parser for ``<resource> <action>`` from a built parser.

    Walks the two levels of ``_SubParsersAction`` (resource → action) so a test
    can introspect the exact parser that composes the shared parent parsers.
    """
    resource_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    resource_parser = resource_action.choices[resource]
    action_action = next(
        a for a in resource_parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    return action_action.choices[action]


def _provider_type_actions(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    """Return every action on *parser* whose option strings include --provider-type."""
    return [a for a in parser._actions if "--provider-type" in getattr(a, "option_strings", [])]


class TestMultiProviderNoConflict:
    """--provider-type is inherited once and never collides when providers grow.

    The global arguments (including --provider-type) live on a single shared
    parent parser that every leaf sub-parser composes via parents=[...].  This
    is the property that keeps the CLI stable as new provider types
    (Azure/GCP/OCI) come online: because the flag is declared once and merely
    inherited, registering additional providers only widens its ``choices`` —
    it never adds a second --provider-type action that argparse would reject
    with a "conflicting option string" error.
    """

    def test_provider_type_declared_once_per_leaf_subparser(self):
        """Every leaf sub-parser inherits exactly one --provider-type action."""
        from orb.cli.args import build_parser

        parser, _ = build_parser()

        for resource, action in [
            ("machines", "list"),
            ("templates", "list"),
            ("requests", "list"),
            ("providers", "list"),
        ]:
            leaf = _leaf_subparser(parser, resource, action)
            actions = _provider_type_actions(leaf)
            assert len(actions) == 1, (
                f"{resource} {action} has {len(actions)} --provider-type actions; "
                "the flag must be inherited exactly once from the shared parent parser."
            )

    def test_second_provider_registering_causes_no_conflict(self):
        """Simulate a second/extra provider type; build_parser must not conflict.

        Injects a synthetic spec into the CLI spec registry (as a real provider
        plugin would) and drives the full build_parser() pipeline.  A duplicate
        --provider-type registration would surface here as ValueError/SystemExit
        at build time; a single inherited flag simply gains a new valid choice.
        """
        from orb.cli.args import build_parser
        from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry

        class _FakeProviderCLISpec:
            def add_arguments(self, parser: argparse.ArgumentParser) -> None:
                parser.add_argument("--fake-region", dest="fake_region", help="fake")

            def extract_config(self, args):
                return {}

            def extract_partial_config(self, args):
                return {}

            def validate_add(self, args):
                return []

            def generate_name(self, args):
                return "fake-instance"

            def format_display(self, config):
                return []

        original_specs = dict(CLISpecRegistry._store)
        try:
            CLISpecRegistry._store["fake-cloud"] = _FakeProviderCLISpec()  # type: ignore[assignment]

            try:
                parser, _ = build_parser()
            except (ValueError, SystemExit) as exc:
                pytest.fail(
                    f"build_parser() raised {type(exc).__name__} with a second provider "
                    f"registered: {exc}. --provider-type must be inherited once, not re-added."
                )

            # Still exactly one --provider-type action per leaf.
            leaf = _leaf_subparser(parser, "machines", "list")
            assert len(_provider_type_actions(leaf)) == 1

            # The new provider type is a valid choice on every command that
            # inherits the provider-scope parent.
            ns = parser.parse_args(["machines", "list", "--provider-type", "fake-cloud"])
            assert ns.provider_type == "fake-cloud"
        finally:
            CLISpecRegistry._store = original_specs


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

        original = dict(CLISpecRegistry._store)
        try:
            CLISpecRegistry._store = {}
            result = _registered_provider_types()
            assert result == []
        finally:
            CLISpecRegistry._store = original
