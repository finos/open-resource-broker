"""Tests for ConsolePort adapter implementations."""

import pytest

from orb.domain.base.ports.console_port import ConsolePort
from orb.infrastructure.adapters.console_adapter import RichConsoleAdapter
from orb.infrastructure.adapters.null_console_adapter import NullConsoleAdapter


def test_null_console_adapter_is_console_port():
    assert isinstance(NullConsoleAdapter(), ConsolePort)


def test_rich_console_adapter_is_console_port():
    assert isinstance(RichConsoleAdapter(), ConsolePort)


def test_null_console_adapter_instantiates_without_error():
    # Would raise TypeError if any abstract method is unimplemented
    adapter = NullConsoleAdapter()
    assert adapter is not None


def test_rich_console_adapter_instantiates_without_error():
    adapter = RichConsoleAdapter()
    assert adapter is not None


@pytest.mark.parametrize("method,args", [
    ("info",      ("msg",)),
    ("success",   ("msg",)),
    ("error",     ("msg",)),
    ("warning",   ("msg",)),
    ("command",   ("msg",)),
    ("separator", ()),
])
def test_null_console_adapter_methods_do_not_raise(method, args):
    adapter = NullConsoleAdapter()
    getattr(adapter, method)(*args)
