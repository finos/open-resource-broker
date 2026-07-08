"""Shared argument-registration helpers for the ORB CLI.

All cross-cutting arguments that appear on more than one subcommand must be
registered through the helpers defined here.  Centralising them in one module
prevents the argparse conflict error (``conflicting option string(s)``) that
arises when the same flag is added twice to the same parser.

Adding a new provider type
--------------------------
New provider types are picked up automatically because ``add_provider_type_arg``
reads the :class:`~orb.infrastructure.registry.cli_spec_registry.CLISpecRegistry`
at parse-construction time.  If the registry is empty (e.g. during a
lightweight unit-test that never calls ``register_all_provider_cli_specs``),
the function falls back to an open-ended ``metavar`` so the CLI still builds.
"""

from __future__ import annotations

import argparse
from typing import Sequence


def add_provider_type_arg(
    parser: argparse.ArgumentParser,
    *,
    default: str | None = None,
    required: bool = False,
    extra_help: str = "",
) -> None:
    """Register ``--provider-type`` on *parser* exactly once.

    The valid choices are derived from the
    :class:`~orb.infrastructure.registry.cli_spec_registry.CLISpecRegistry`
    so new providers are reflected automatically without any edit here.

    When the registry is empty the argument is registered without ``choices``
    (any value is accepted) so that early-bootstrap callers (e.g. unit tests
    that never perform full provider initialisation) still work.

    Args:
        parser: The :class:`argparse.ArgumentParser` (or sub-parser) to
            register the argument on.
        default: Value to use when the flag is omitted.  ``None`` means the
            attribute is absent from the resulting :class:`argparse.Namespace`
            unless the user supplies a value.
        required: When ``True`` argparse will error if the flag is omitted.
        extra_help: Additional text appended to the stock help string.
    """
    choices: Sequence[str] | None = _registered_provider_types()

    base_help = "Restrict to all active instances of a provider type (e.g. aws, k8s)"
    help_text = f"{base_help}{'. ' + extra_help if extra_help else ''}"

    kwargs: dict = {
        "dest": "provider_type",
        "metavar": "TYPE",
        "help": help_text,
        "default": default,
        "required": required,
    }
    if choices:
        kwargs["choices"] = list(choices)

    parser.add_argument("--provider-type", **kwargs)


def _registered_provider_types() -> list[str]:
    """Return the sorted list of provider types known to the CLI spec registry.

    Returns an empty list when the registry has not been populated yet (e.g.
    in unit tests that skip the full bootstrap).
    """
    try:
        from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry

        return sorted(CLISpecRegistry.all().keys())
    except Exception:  # pragma: no cover — defensive
        return []
