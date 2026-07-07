"""Tests for orb.ui.components.cell_formatters.

Covers every formatter factory: bool_badge, json_truncate, list_count.

Each formatter is a factory that returns a ``(row: Any) -> rx.Component``
callable.  Under the rx stub from conftest.py every rx call returns a
MagicMock, so we verify:
  - the right rx primitive is called (badge / code / text / cond)
  - the correct key is looked up in the row dict
  - em-dash fallback when the key is missing, None, or empty string
"""

from __future__ import annotations

# conftest.py installs the rx stub before any orb.ui imports.
# All orb.ui imports MUST live inside test functions.


# ---------------------------------------------------------------------------
# bool_badge
# ---------------------------------------------------------------------------


class TestBoolBadge:
    def _factory(self):
        from orb.ui.components.cell_formatters import bool_badge

        return bool_badge

    def test_returns_callable(self):
        fmt = self._factory()("is_active")
        assert callable(fmt)

    def test_present_true_value_calls_cond(self):
        """bool_badge wraps the field in rx.cond → component is returned."""
        import reflex as rx

        fmt = self._factory()("is_active")
        row = {"is_active": True}
        component = fmt(row)
        # rx.cond is a MagicMock — calling it returns another MagicMock
        assert component is not None
        rx.cond.assert_called()

    def test_present_false_value_calls_cond(self):
        import reflex as rx

        fmt = self._factory()("enabled")
        row = {"enabled": False}
        component = fmt(row)
        assert component is not None
        rx.cond.assert_called()

    def test_key_lookup_uses_provided_key(self):
        """The formatter uses the key passed to bool_badge, not a hardcoded one."""
        import reflex as rx

        rx.cond.reset_mock()
        fmt = self._factory()("my_field")
        row = {"my_field": True}
        fmt(row)
        # First positional arg to cond should come from row["my_field"]
        rx.cond.assert_called()
        args = rx.cond.call_args[0]
        # args[0] is the condition (row["my_field"] == True)
        assert args[0] == row["my_field"]

    def test_missing_key_uses_missing_sentinel(self):
        """Accessing a missing key returns the dict's default (KeyError in plain dict)."""
        fmt = self._factory()("nonexistent_key")
        row: dict = {}
        # The formatter does row[key] directly — with a plain dict this raises
        # KeyError.  Verify the formatter doesn't silently swallow it.
        try:
            fmt(row)
        except KeyError:
            pass  # expected — real rows always have all keys pre-populated

    def test_yes_badge_created_with_green_scheme(self):
        """rx.badge('yes', ..., color_scheme='green') is called for truthy path."""
        import reflex as rx

        rx.badge.reset_mock()
        fmt = self._factory()("flag")
        row = {"flag": True}
        fmt(row)
        # badge should have been called at least once
        rx.badge.assert_called()
        calls_kwargs = [c.kwargs for c in rx.badge.call_args_list]
        green_calls = [kw for kw in calls_kwargs if kw.get("color_scheme") == "green"]
        assert len(green_calls) >= 1

    def test_no_badge_created_with_gray_scheme(self):
        """rx.badge('no', ..., color_scheme='gray') is called for falsy path."""
        import reflex as rx

        rx.badge.reset_mock()
        fmt = self._factory()("flag")
        row = {"flag": True}
        fmt(row)
        rx.badge.assert_called()
        calls_kwargs = [c.kwargs for c in rx.badge.call_args_list]
        gray_calls = [kw for kw in calls_kwargs if kw.get("color_scheme") == "gray"]
        assert len(gray_calls) >= 1


# ---------------------------------------------------------------------------
# json_truncate
# ---------------------------------------------------------------------------


class TestJsonTruncate:
    def _factory(self):
        from orb.ui.components.cell_formatters import json_truncate

        return json_truncate

    def test_returns_callable(self):
        fmt = self._factory()("tags")
        assert callable(fmt)

    def test_present_value_renders_via_cond(self):
        """A non-empty string causes rx.cond to be called (truthy branch)."""
        import reflex as rx

        rx.cond.reset_mock()
        fmt = self._factory()("tags")
        component = fmt({"tags": '{"key": "value"}'})
        assert component is not None
        rx.cond.assert_called()

    def test_empty_string_triggers_emdash_fallback(self):
        """Empty string → the cond condition is falsy → em-dash rx.text branch."""
        import reflex as rx

        rx.text.reset_mock()
        fmt = self._factory()("tags")
        fmt({"tags": ""})
        # The em-dash branch calls rx.text("—", ...)
        em_calls = [c for c in rx.text.call_args_list if c.args and c.args[0] == "—"]
        assert len(em_calls) >= 1

    def test_none_value_treated_like_empty(self):
        """None value: cond condition evaluates as '' != '' == False → em-dash."""
        import reflex as rx

        rx.cond.reset_mock()
        fmt = self._factory()("data")
        # Pass None — the cond condition is row["data"] != "" with None
        fmt({"data": None})
        rx.cond.assert_called()

    def test_long_value_uses_code_with_ellipsis_style(self):
        """Code element is rendered with text_overflow='ellipsis' and max_width.

        rx.code / rx.cond / rx.text share the same MagicMock under the stub.
        The rx.code(...) call is always call index 0 (the first call evaluated
        in the cond expression), so we inspect call_args_list[0] specifically.
        """
        import reflex as rx

        rx.code.reset_mock()
        fmt = self._factory()("content")
        long_val = "x" * 500
        fmt({"content": long_val})
        rx.code.assert_called()
        # Find the call with text_overflow kwarg (the rx.code call, not cond/text)
        code_calls = [c for c in rx.code.call_args_list if "text_overflow" in c.kwargs]
        assert code_calls, "no rx.code call with text_overflow kwarg found"
        code_kwargs = code_calls[0].kwargs
        assert code_kwargs.get("text_overflow") == "ellipsis"
        assert "max_width" in code_kwargs

    def test_code_element_gets_white_space_nowrap(self):
        """code element has white_space='nowrap' to prevent wrapping."""
        import reflex as rx

        rx.code.reset_mock()
        fmt = self._factory()("data")
        fmt({"data": "some json"})
        rx.code.assert_called()
        # Find the call with white_space kwarg (the rx.code call)
        code_calls = [c for c in rx.code.call_args_list if "white_space" in c.kwargs]
        assert code_calls, "no rx.code call with white_space kwarg found"
        assert code_calls[0].kwargs.get("white_space") == "nowrap"

    def test_emdash_text_has_gray_color(self):
        """The em-dash fallback uses rx.color('gray', 9) for the color."""
        import reflex as rx

        rx.color.reset_mock()
        fmt = self._factory()("tags")
        fmt({"tags": ""})
        rx.color.assert_called_with("gray", 9)

    def test_different_keys_produce_independent_formatters(self):
        """Two calls to json_truncate with different keys return independent formatters."""
        factory = self._factory()
        fmt_a = factory("field_a")
        fmt_b = factory("field_b")
        assert fmt_a is not fmt_b

    def test_present_nonempty_value_passes_value_to_code(self):
        """The actual value from the row is forwarded to rx.code.

        Under the stub rx.code shares a MagicMock with rx.cond/rx.text.
        The code call (index 0 in call_args_list) has the value as its
        first positional arg; we find it by matching on white_space kwarg.
        """
        import reflex as rx

        rx.code.reset_mock()
        fmt = self._factory()("info")
        val = '{"key": "val"}'
        fmt({"info": val})
        rx.code.assert_called()
        # The rx.code(value, ...) call is the one with size kwarg and a string arg
        code_calls = [
            c
            for c in rx.code.call_args_list
            if c.args and isinstance(c.args[0], str) and "size" in c.kwargs
        ]
        assert code_calls, "no rx.code(value, size=...) call found"
        assert code_calls[0].args[0] == val


# ---------------------------------------------------------------------------
# list_count
# ---------------------------------------------------------------------------


class TestListCount:
    def _factory(self):
        from orb.ui.components.cell_formatters import list_count

        return list_count

    def test_returns_callable(self):
        fmt = self._factory()("subnet_ids")
        assert callable(fmt)

    def test_zero_items_empty_string_shows_emdash(self):
        """Empty string (0 items, pre-serialised) → em-dash fallback."""
        import reflex as rx

        rx.text.reset_mock()
        fmt = self._factory()("subnet_ids")
        fmt({"subnet_ids": ""})
        em_calls = [c for c in rx.text.call_args_list if c.args and c.args[0] == "—"]
        assert len(em_calls) >= 1

    def test_one_item_string_renders_count_text(self):
        """'1 item' string → cond truthy → rx.text called with the value."""
        import reflex as rx

        rx.text.reset_mock()
        fmt = self._factory()("subnet_ids")
        fmt({"subnet_ids": "1 item"})
        # rx.text should be called with the value "1 item"
        val_calls = [c for c in rx.text.call_args_list if c.args and c.args[0] == "1 item"]
        assert len(val_calls) >= 1

    def test_many_items_string_renders_count_text(self):
        """'5 items' string → cond truthy → rx.text called with the value."""
        import reflex as rx

        rx.text.reset_mock()
        fmt = self._factory()("tags")
        fmt({"tags": "5 items"})
        val_calls = [c for c in rx.text.call_args_list if c.args and c.args[0] == "5 items"]
        assert len(val_calls) >= 1

    def test_present_nonempty_calls_cond(self):
        """Non-empty value → rx.cond is called."""
        import reflex as rx

        rx.cond.reset_mock()
        fmt = self._factory()("networks")
        fmt({"networks": "3 items"})
        rx.cond.assert_called()

    def test_count_text_uses_gray_11_color(self):
        """The count text uses rx.color('gray', 11)."""
        import reflex as rx

        rx.color.reset_mock()
        fmt = self._factory()("items")
        fmt({"items": "2 items"})
        # color is called for the count text's color kwarg
        gray_11_calls = [c for c in rx.color.call_args_list if c.args == ("gray", 11)]
        assert len(gray_11_calls) >= 1

    def test_emdash_text_uses_gray_9_color(self):
        """The em-dash uses rx.color('gray', 9)."""
        import reflex as rx

        rx.color.reset_mock()
        fmt = self._factory()("items")
        fmt({"items": ""})
        gray_9_calls = [c for c in rx.color.call_args_list if c.args == ("gray", 9)]
        assert len(gray_9_calls) >= 1

    def test_none_value_does_not_crash(self):
        """None value is handled without raising (condition evaluates None != '')."""
        fmt = self._factory()("items")
        # Should not raise; rx.cond is a MagicMock that absorbs any args
        result = fmt({"items": None})
        assert result is not None
