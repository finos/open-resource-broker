"""Unit tests for ExceptionTypeMapper.

Coverage targets: lines 47,79,99-100,103-105,108-109,111,123-124,127-129,
131,143-144,147-149,151,155-156,158-159,168
"""

from __future__ import annotations

import pytest

from orb.infrastructure.error.exception_type_mapper import ExceptionTypeMapper

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Base(Exception):
    pass


class _Child(_Base):
    pass


class _GrandChild(_Child):
    pass


def _handler_a(exc, *a, **kw):
    return "handler_a"


def _handler_b(exc, *a, **kw):
    return "handler_b"


def _fallback(exc, *a, **kw):
    return "fallback"


# ---------------------------------------------------------------------------
# register_handler / get_handler
# ---------------------------------------------------------------------------


class TestRegisterAndGetHandler:
    def test_exact_match_returns_registered_handler(self):
        mapper = ExceptionTypeMapper()
        mapper.register_handler(ValueError, _handler_a)
        assert mapper.get_handler(ValueError) is _handler_a

    def test_parent_class_handler_matches_child(self):
        mapper = ExceptionTypeMapper()
        mapper.register_handler(_Base, _handler_a)
        assert mapper.get_handler(_Child) is _handler_a

    def test_exact_match_wins_over_parent(self):
        mapper = ExceptionTypeMapper()
        mapper.register_handler(_Base, _handler_a)
        mapper.register_handler(_Child, _handler_b)
        assert mapper.get_handler(_Child) is _handler_b

    def test_grandchild_falls_back_through_mro(self):
        mapper = ExceptionTypeMapper()
        mapper.register_handler(_Base, _handler_a)
        assert mapper.get_handler(_GrandChild) is _handler_a

    def test_no_handler_with_fallback_returns_fallback(self):
        mapper = ExceptionTypeMapper()
        result = mapper.get_handler(RuntimeError, fallback_handler=_fallback)
        assert result is _fallback

    def test_no_handler_without_fallback_raises(self):
        mapper = ExceptionTypeMapper()
        with pytest.raises(ValueError, match="No handler found"):
            mapper.get_handler(RuntimeError)

    def test_cache_is_used_on_second_call(self):
        mapper = ExceptionTypeMapper()
        mapper.register_handler(_Base, _handler_a)
        first = mapper.get_handler(_Child)
        second = mapper.get_handler(_Child)
        assert first is second


# ---------------------------------------------------------------------------
# register_http_handler / get_http_handler
# ---------------------------------------------------------------------------


class TestRegisterAndGetHttpHandler:
    def test_exact_match_returns_registered_http_handler(self):
        mapper = ExceptionTypeMapper()
        mapper.register_http_handler(ValueError, _handler_a)
        assert mapper.get_http_handler(ValueError) is _handler_a

    def test_parent_handler_matches_child_via_mro(self):
        mapper = ExceptionTypeMapper()
        mapper.register_http_handler(_Base, _handler_b)
        assert mapper.get_http_handler(_Child) is _handler_b

    def test_no_http_handler_with_fallback(self):
        mapper = ExceptionTypeMapper()
        result = mapper.get_http_handler(KeyError, fallback_handler=_fallback)
        assert result is _fallback

    def test_no_http_handler_without_fallback_raises(self):
        mapper = ExceptionTypeMapper()
        with pytest.raises(ValueError, match="No HTTP handler found"):
            mapper.get_http_handler(KeyError)

    def test_grandchild_mro_lookup(self):
        mapper = ExceptionTypeMapper()
        mapper.register_http_handler(_Base, _handler_a)
        assert mapper.get_http_handler(_GrandChild) is _handler_a


# ---------------------------------------------------------------------------
# has_handler / has_http_handler
# ---------------------------------------------------------------------------


class TestHasHandler:
    def test_returns_true_for_exact_type(self):
        mapper = ExceptionTypeMapper()
        mapper.register_handler(ValueError, _handler_a)
        assert mapper.has_handler(ValueError) is True

    def test_returns_true_for_subclass_via_mro(self):
        mapper = ExceptionTypeMapper()
        mapper.register_handler(_Base, _handler_a)
        assert mapper.has_handler(_Child) is True

    def test_returns_false_when_not_registered(self):
        mapper = ExceptionTypeMapper()
        assert mapper.has_handler(RuntimeError) is False

    def test_has_http_handler_exact(self):
        mapper = ExceptionTypeMapper()
        mapper.register_http_handler(_Base, _handler_b)
        assert mapper.has_http_handler(_Base) is True

    def test_has_http_handler_child(self):
        mapper = ExceptionTypeMapper()
        mapper.register_http_handler(_Base, _handler_b)
        assert mapper.has_http_handler(_Child) is True

    def test_has_http_handler_false(self):
        mapper = ExceptionTypeMapper()
        assert mapper.has_http_handler(ValueError) is False


# ---------------------------------------------------------------------------
# clear_handlers
# ---------------------------------------------------------------------------


class TestClearHandlers:
    def test_clears_all_handlers(self):
        mapper = ExceptionTypeMapper()
        mapper.register_handler(ValueError, _handler_a)
        mapper.register_http_handler(KeyError, _handler_b)
        mapper.clear_handlers()
        assert mapper.has_handler(ValueError) is False
        assert mapper.has_http_handler(KeyError) is False

    def test_cache_is_cleared_after_clear(self):
        mapper = ExceptionTypeMapper()
        mapper.register_handler(_Base, _handler_a)
        _ = mapper.get_handler(_Child)  # populate cache
        mapper.clear_handlers()
        # After clear, registering a new handler should be reflected
        mapper.register_handler(_Base, _handler_b)
        assert mapper.get_handler(_Child) is _handler_b


# ---------------------------------------------------------------------------
# get_registered_types
# ---------------------------------------------------------------------------


class TestGetRegisteredTypes:
    def test_returns_union_of_both_dicts(self):
        mapper = ExceptionTypeMapper()
        mapper.register_handler(ValueError, _handler_a)
        mapper.register_http_handler(KeyError, _handler_b)
        types = mapper.get_registered_types()
        assert ValueError in types
        assert KeyError in types

    def test_empty_when_nothing_registered(self):
        mapper = ExceptionTypeMapper()
        assert mapper.get_registered_types() == set()
