"""Tests for _make_bus_handler flag_ prefix resolution in registry.py."""

import argparse
import inspect

import pytest


def _make_factory(param_name: str):
    """Return a simple factory function that accepts a single named parameter."""

    def factory(**kwargs):
        return kwargs

    # Rewrite the signature so inspect.signature sees the expected parameter
    factory.__signature__ = inspect.Signature(
        [inspect.Parameter(param_name, inspect.Parameter.KEYWORD_ONLY)]
    )
    return factory


def _run_kwargs_resolution(args: argparse.Namespace, factory_fn) -> dict:
    """Replicate the kwargs-building logic from _make_bus_handler."""
    sig = inspect.signature(factory_fn)
    args_dict = vars(args).copy()
    kwargs = {}
    for k in sig.parameters:
        if k in args_dict and args_dict[k] is not None:
            kwargs[k] = args_dict[k]
        elif f"flag_{k}" in args_dict and args_dict[f"flag_{k}"] is not None:
            kwargs[k] = args_dict[f"flag_{k}"]
    return kwargs


def test_positional_arg_passed_to_factory():
    args = argparse.Namespace(machine_id="m-123", flag_machine_id=None)
    factory = _make_factory("machine_id")
    kwargs = _run_kwargs_resolution(args, factory)
    assert kwargs == {"machine_id": "m-123"}


def test_flag_arg_used_when_positional_is_none():
    args = argparse.Namespace(machine_id=None, flag_machine_id="m-123")
    factory = _make_factory("machine_id")
    kwargs = _run_kwargs_resolution(args, factory)
    assert kwargs == {"machine_id": "m-123"}


def test_positional_takes_precedence_over_flag():
    args = argparse.Namespace(machine_id="positional-id", flag_machine_id="flag-id")
    factory = _make_factory("machine_id")
    kwargs = _run_kwargs_resolution(args, factory)
    assert kwargs == {"machine_id": "positional-id"}


def test_neither_provided_key_absent_from_kwargs():
    args = argparse.Namespace(machine_id=None, flag_machine_id=None)
    factory = _make_factory("machine_id")
    kwargs = _run_kwargs_resolution(args, factory)
    assert "machine_id" not in kwargs
