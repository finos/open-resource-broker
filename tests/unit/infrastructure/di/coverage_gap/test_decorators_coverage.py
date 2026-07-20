"""Unit tests for orb.infrastructure.di.decorators (the standalone injectable decorator module).

Coverage targets: lines 49-51,56-58,65,80-82,85-87,90-92,94,97,99,104-105,
110-111,152,155-156,159-161,164-165,167,186-187,189,191,194-195,198-199,205,
207-210,217,220-221,227,230-233,240,242-243,246,251-253,255-256,261-262,267,
272-273,275-277,279-282,284-286,295,300-302
"""

from __future__ import annotations

import inspect
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest

from orb.infrastructure.di.decorators import (
    _extract_optional_inner_type,
    _is_called_from_di_container,
    _is_optional_type,
    _is_primitive_type,
    _resolve_dependency,
    get_injectable_info,
    injectable,
    is_injectable,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# injectable decorator
# ---------------------------------------------------------------------------


class TestInjectableDecorator:
    def test_marks_class_injectable(self):
        @injectable
        class Svc:
            def __init__(self) -> None:
                pass

        assert is_injectable(Svc)

    def test_sets_original_init(self):
        @injectable
        class Svc:
            def __init__(self) -> None:
                pass

        assert hasattr(Svc, "_original_init")

    def test_positional_args_bypass_resolution(self):
        """When positional args are given, the original constructor is called directly."""

        @injectable
        class Svc:
            def __init__(self, value: str = "") -> None:
                self.value = value

        obj = Svc("hello")
        assert obj.value == "hello"

    def test_kwarg_passed_directly_is_used(self):
        """Explicitly provided kwargs take priority over container resolution."""

        @injectable
        class Svc:
            def __init__(self, name: str = "default") -> None:
                self.name = name

        obj = Svc(name="custom")
        assert obj.name == "custom"

    def test_param_with_default_and_no_hint_uses_default(self):
        """Parameter with no type hint and a default — should fall back to default."""

        @injectable
        class Svc:
            def __init__(self, x=99) -> None:
                self.x = x

        obj = Svc()
        assert obj.x == 99

    def test_failing_init_logs_and_reraises(self):
        """If original_init raises, the decorator should re-raise."""

        @injectable
        class Svc:
            def __init__(self) -> None:
                raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            Svc()

    def test_class_with_bad_signature_returns_class_unchanged(self):
        """If inspect.signature fails, return cls unchanged."""

        class BadSig:
            pass

        # Simulate a class whose __init__ can't be inspected
        orig_sig = inspect.signature
        call_count = 0

        def mock_sig(obj):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("cannot inspect")
            return orig_sig(obj)

        with patch("orb.infrastructure.di.decorators.inspect.signature", side_effect=mock_sig):
            result = injectable(BadSig)
        # Class returned unchanged (no _injectable attribute set via the failing path)
        assert result is BadSig


# ---------------------------------------------------------------------------
# _is_called_from_di_container
# ---------------------------------------------------------------------------


class TestIsCalledFromDiContainer:
    def test_returns_false_in_normal_context(self):
        # Not called from any DI module
        result = _is_called_from_di_container()
        assert result is False


# ---------------------------------------------------------------------------
# _is_primitive_type
# ---------------------------------------------------------------------------


class TestIsPrimitiveType:
    def test_str_is_primitive(self):
        assert _is_primitive_type(str) is True

    def test_int_is_primitive(self):
        assert _is_primitive_type(int) is True

    def test_float_is_primitive(self):
        assert _is_primitive_type(float) is True

    def test_bool_is_primitive(self):
        assert _is_primitive_type(bool) is True

    def test_bytes_is_primitive(self):
        assert _is_primitive_type(bytes) is True

    def test_none_type_is_primitive(self):
        assert _is_primitive_type(type(None)) is True

    def test_custom_class_is_not_primitive(self):
        class Custom:
            pass

        assert _is_primitive_type(Custom) is False

    def test_list_str_generic_is_not_primitive(self):
        # List[str]'s origin is `list`, which is not in the primitive_types set in decorators.py
        assert _is_primitive_type(List[str]) is False  # type: ignore[valid-type]

    def test_any_type_is_primitive(self):
        from typing import Any

        assert _is_primitive_type(Any) is True


# ---------------------------------------------------------------------------
# _is_optional_type / _extract_optional_inner_type
# ---------------------------------------------------------------------------


class TestOptionalTypeHelpers:
    def test_optional_str_is_optional(self):
        assert _is_optional_type(Optional[str]) is True

    def test_plain_str_is_not_optional(self):
        assert _is_optional_type(str) is False

    def test_extract_inner_from_optional_str(self):
        inner = _extract_optional_inner_type(Optional[str])
        assert inner is str

    def test_extract_inner_from_optional_int(self):
        inner = _extract_optional_inner_type(Optional[int])
        assert inner is int


# ---------------------------------------------------------------------------
# _resolve_dependency
# ---------------------------------------------------------------------------


class _FakeDep:
    pass


class TestResolveDependency:
    def _make_param(self, default=inspect.Parameter.empty) -> inspect.Parameter:
        return inspect.Parameter("dep", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=default)

    def test_primitive_type_returns_default(self):
        param = self._make_param(default="hello")
        fake_container = MagicMock()
        result = _resolve_dependency(str, param, "MyClass", "my_param", container=fake_container)
        # Primitive types skip DI resolution and return the param default without
        # ever consulting the container.
        assert result == "hello"
        fake_container.get.assert_not_called()

    def test_optional_primitive_returns_default(self):
        param = self._make_param(default=42)
        fake_container = MagicMock()
        result = _resolve_dependency(
            Optional[int], param, "MyClass", "my_param", container=fake_container
        )
        assert result == 42

    def test_optional_custom_type_resolved_from_container(self):
        instance = _FakeDep()
        fake_container = MagicMock()
        fake_container.get.return_value = instance
        param = self._make_param()
        result = _resolve_dependency(
            Optional[_FakeDep], param, "MyClass", "my_param", container=fake_container
        )
        assert result is instance

    def test_optional_custom_type_container_raises_returns_default(self):
        fake_container = MagicMock()
        fake_container.get.side_effect = Exception("not registered")
        param = self._make_param(default=None)
        result = _resolve_dependency(
            Optional[_FakeDep], param, "MyClass", "my_param", container=fake_container
        )
        assert result is None

    def test_regular_type_resolved_from_container(self):
        instance = _FakeDep()
        fake_container = MagicMock()
        fake_container.get.return_value = instance
        param = self._make_param()
        result = _resolve_dependency(
            _FakeDep, param, "MyClass", "my_param", container=fake_container
        )
        assert result is instance

    def test_regular_type_container_raises_returns_none(self):
        fake_container = MagicMock()
        fake_container.get.side_effect = Exception("not found")
        param = self._make_param()
        result = _resolve_dependency(
            _FakeDep, param, "MyClass", "my_param", container=fake_container
        )
        assert result is None

    def test_outer_exception_returns_none(self):
        # Force outer exception — pass a container that raises on .get()
        # AND an annotation that makes _is_optional_type/_is_primitive_type both False
        # so we reach container.get(annotation) which fails → outer except returns None
        class _Custom:
            pass

        fake_container = MagicMock()
        fake_container.get.side_effect = Exception("unexpected crash")
        param = self._make_param()
        result = _resolve_dependency(_Custom, param, "MyClass", "dep", container=fake_container)
        assert result is None


# ---------------------------------------------------------------------------
# get_injectable_info
# ---------------------------------------------------------------------------


class _GlobalDep:
    """Module-level dep for get_injectable_info (get_type_hints needs global resolution)."""

    pass


class TestGetInjectableInfo:
    def test_returns_empty_for_non_injectable_class(self):
        class Plain:
            pass

        assert get_injectable_info(Plain) == {}

    def test_returns_info_for_injectable_class(self):
        @injectable
        class Svc:
            def __init__(self, dep: _GlobalDep) -> None:
                self.dep = dep

        info = get_injectable_info(Svc)
        assert info["class_name"] == "Svc"
        assert "dep" in info["dependencies"]
        assert info["total_dependencies"] == 1

    def test_optional_dependency_marked_as_optional(self):
        @injectable
        class Svc:
            def __init__(self, dep: Optional[_GlobalDep] = None) -> None:
                self.dep = dep

        info = get_injectable_info(Svc)
        dep_info = info["dependencies"]["dep"]
        assert dep_info["optional"] is True
        assert dep_info["has_default"] is True

    def test_error_in_info_extraction_returns_error_dict(self):
        # Simulate a class that is_injectable but whose _original_init triggers an error
        class FakeBroken:
            _injectable = True

            @staticmethod
            def _original_init():
                pass  # type: ignore[return]

        # Monkey-patch to trigger the except branch
        orig = FakeBroken._original_init
        FakeBroken._original_init = None  # type: ignore[assignment]
        result = get_injectable_info(FakeBroken)
        FakeBroken._original_init = orig
        # The except branch returns a dict with BOTH class_name and error keys;
        # require the error key specifically to distinguish it from the happy path.
        assert "error" in result
        assert result["class_name"] == "FakeBroken"
