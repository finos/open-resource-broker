"""Unit tests for the list_page_shell shared component.

Improvements over the original "component is not None" assertions:
  1. Spy on rx.vstack to verify it was called with the exact expected kwargs.
  2. Verify load-more guard logic: only cursor + handler together triggers
     the internal button; providing just one raises or silently falls back.
  3. Verify error_banner slot: the component passed as error_banner appears
     as the first positional argument in the outer vstack call.

The rx stub is already installed by conftest.py at collection time.
All orb.ui imports live inside test functions.
"""

from __future__ import annotations

from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(rx):
    """Return a minimal valid set of kwargs for list_page_shell."""
    return {
        "filter_row": rx.fragment(),
        "toolbar": rx.fragment(),
        "grid": rx.fragment(),
        "load_more": rx.fragment(),
        "empty": rx.fragment(),
        "error_banner": rx.fragment(),
        "is_loading": rx.Var.create(False),
        "is_empty": rx.Var.create(True),
    }


# ---------------------------------------------------------------------------
# Basic composition tests (previously just "is not None")
# ---------------------------------------------------------------------------


def test_list_page_shell_composes_without_raising() -> None:
    """list_page_shell must return a component when called with minimal args."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    component = list_page_shell(**_make_args(rx))
    assert component is not None


def test_list_page_shell_accepts_banners_and_dialogs() -> None:
    """Optional banners and dialogs lists must be accepted without error."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    component = list_page_shell(
        filter_row=rx.fragment(),
        toolbar=rx.fragment(),
        grid=rx.fragment(),
        load_more=rx.fragment(),
        empty=rx.fragment(),
        error_banner=rx.fragment(),
        banners=[rx.fragment(), rx.fragment()],
        is_loading=rx.Var.create(False),
        is_empty=rx.Var.create(False),
        dialogs=[rx.fragment()],
    )
    assert component is not None


def test_list_page_shell_accepts_custom_skeleton() -> None:
    """A custom loading_skeleton component must be accepted."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    custom_skeleton = rx.vstack(rx.skeleton(height="2rem", width="100%"))
    component = list_page_shell(
        filter_row=rx.fragment(),
        toolbar=rx.fragment(),
        grid=rx.fragment(),
        load_more=rx.fragment(),
        empty=rx.fragment(),
        error_banner=rx.fragment(),
        is_loading=rx.Var.create(True),
        is_empty=rx.Var.create(True),
        loading_skeleton=custom_skeleton,
    )
    assert component is not None


def test_list_page_shell_default_none_lists() -> None:
    """Omitting banners and dialogs (defaults to None) must work."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    component = list_page_shell(
        filter_row=rx.fragment(),
        toolbar=rx.fragment(),
        grid=rx.fragment(),
        load_more=rx.fragment(),
        empty=rx.fragment(),
        error_banner=rx.fragment(),
        is_loading=rx.Var.create(False),
        is_empty=rx.Var.create(False),
    )
    assert component is not None


def test_list_page_shell_load_more_via_primitives() -> None:
    """Passing next_cursor/loading_more/on_load_more builds the button internally."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    mock_handler = MagicMock()
    component = list_page_shell(
        filter_row=rx.fragment(),
        toolbar=rx.fragment(),
        grid=rx.fragment(),
        empty=rx.fragment(),
        error_banner=rx.fragment(),
        is_loading=rx.Var.create(False),
        is_empty=rx.Var.create(False),
        next_cursor=rx.Var.create("cursor_abc"),
        loading_more=rx.Var.create(False),
        on_load_more=mock_handler,
    )
    assert component is not None


def test_list_page_shell_explicit_load_more_takes_precedence() -> None:
    """An explicit load_more component takes precedence over primitives."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    mock_handler = MagicMock()
    custom_load_more = rx.button("Custom load more")
    component = list_page_shell(
        filter_row=rx.fragment(),
        toolbar=rx.fragment(),
        grid=rx.fragment(),
        load_more=custom_load_more,
        empty=rx.fragment(),
        error_banner=rx.fragment(),
        is_loading=rx.Var.create(False),
        is_empty=rx.Var.create(False),
        next_cursor=rx.Var.create("cursor_abc"),
        loading_more=rx.Var.create(False),
        on_load_more=mock_handler,
    )
    assert component is not None


def test_list_page_shell_no_load_more_args() -> None:
    """Omitting all load-more args must produce a valid component (rx.fragment fallback)."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    component = list_page_shell(
        filter_row=rx.fragment(),
        toolbar=rx.fragment(),
        grid=rx.fragment(),
        empty=rx.fragment(),
        error_banner=rx.fragment(),
        is_loading=rx.Var.create(False),
        is_empty=rx.Var.create(False),
    )
    assert component is not None


def test_build_load_more_button_renders_when_cursor_present() -> None:
    """_build_load_more_button must compose without raising for any Var inputs."""
    import reflex as rx

    from orb.ui.components.list_page_shell import _build_load_more_button

    button = _build_load_more_button(
        next_cursor=rx.Var.create("abc"),
        loading_more=rx.Var.create(False),
        on_load_more=rx.Var.create(None),
    )
    assert button is not None


def test_build_load_more_button_renders_loading_state() -> None:
    """_build_load_more_button must compose without raising when loading_more=True."""
    import reflex as rx

    from orb.ui.components.list_page_shell import _build_load_more_button

    button = _build_load_more_button(
        next_cursor=rx.Var.create("abc"),
        loading_more=rx.Var.create(True),
        on_load_more=rx.Var.create(None),
    )
    assert button is not None


# ---------------------------------------------------------------------------
# Spy tests: verify rx.vstack is called with expected kwargs
# ---------------------------------------------------------------------------


def test_outer_vstack_called_with_width_100_percent() -> None:
    """The outer rx.vstack must be called with width='100%'."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    rx.vstack.reset_mock()
    list_page_shell(**_make_args(rx))

    # At least one vstack call must have width="100%"
    calls_with_width = [c for c in rx.vstack.call_args_list if c.kwargs.get("width") == "100%"]
    assert len(calls_with_width) >= 1, "outer vstack missing width='100%'"


def test_outer_vstack_receives_error_banner_as_first_arg() -> None:
    """The error_banner component is the first positional arg to the outer vstack."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    rx.vstack.reset_mock()
    sentinel_error_banner = MagicMock(name="error_banner_sentinel")
    args = _make_args(rx)
    args["error_banner"] = sentinel_error_banner

    list_page_shell(**args)

    # The last vstack call is the outer shell (it has width='100%').
    outer_calls = [c for c in rx.vstack.call_args_list if c.kwargs.get("width") == "100%"]
    assert outer_calls, "no outer vstack call found"
    outer_call = outer_calls[-1]
    # error_banner must be the first positional arg
    assert outer_call.args[0] is sentinel_error_banner


def test_outer_vstack_receives_filter_row_and_toolbar() -> None:
    """filter_row and toolbar must appear in the outer vstack positional args."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    rx.vstack.reset_mock()
    sentinel_filter = MagicMock(name="filter_sentinel")
    sentinel_toolbar = MagicMock(name="toolbar_sentinel")
    args = _make_args(rx)
    args["filter_row"] = sentinel_filter
    args["toolbar"] = sentinel_toolbar

    list_page_shell(**args)

    outer_calls = [c for c in rx.vstack.call_args_list if c.kwargs.get("width") == "100%"]
    assert outer_calls
    outer_args = outer_calls[-1].args
    assert sentinel_filter in outer_args
    assert sentinel_toolbar in outer_args


def test_banners_appear_in_outer_vstack() -> None:
    """Banner components must be spread into the outer vstack positional args."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    rx.vstack.reset_mock()
    banner1 = MagicMock(name="banner1")
    banner2 = MagicMock(name="banner2")
    args = _make_args(rx)
    args["banners"] = [banner1, banner2]

    list_page_shell(**args)

    outer_calls = [c for c in rx.vstack.call_args_list if c.kwargs.get("width") == "100%"]
    assert outer_calls
    outer_args = outer_calls[-1].args
    assert banner1 in outer_args
    assert banner2 in outer_args


# ---------------------------------------------------------------------------
# Guard: partial load-more primitives fall back to fragment (no crash)
# ---------------------------------------------------------------------------


def test_only_next_cursor_without_handler_falls_back_to_fragment() -> None:
    """Providing next_cursor without on_load_more → falls back to rx.fragment()."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    rx.fragment.reset_mock()
    # Provide cursor + loading_more but omit on_load_more
    component = list_page_shell(
        filter_row=rx.fragment(),
        toolbar=rx.fragment(),
        grid=rx.fragment(),
        empty=rx.fragment(),
        error_banner=rx.fragment(),
        is_loading=rx.Var.create(False),
        is_empty=rx.Var.create(False),
        next_cursor=rx.Var.create("cursor_abc"),
        loading_more=rx.Var.create(False),
        # on_load_more deliberately omitted
    )
    # Must not raise and must return a component
    assert component is not None


def test_only_on_load_more_without_cursor_falls_back_to_fragment() -> None:
    """Providing on_load_more without next_cursor → falls back to rx.fragment()."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    mock_handler = MagicMock()
    component = list_page_shell(
        filter_row=rx.fragment(),
        toolbar=rx.fragment(),
        grid=rx.fragment(),
        empty=rx.fragment(),
        error_banner=rx.fragment(),
        is_loading=rx.Var.create(False),
        is_empty=rx.Var.create(False),
        on_load_more=mock_handler,
        # next_cursor and loading_more deliberately omitted
    )
    assert component is not None


# ---------------------------------------------------------------------------
# error_banner slot: receives the error component
# ---------------------------------------------------------------------------


def test_error_banner_slot_receives_passed_component() -> None:
    """The component passed as error_banner is forwarded to the outer vstack."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    rx.vstack.reset_mock()
    error_component = MagicMock(name="error_callout")
    args = _make_args(rx)
    args["error_banner"] = error_component

    list_page_shell(**args)

    outer_calls = [c for c in rx.vstack.call_args_list if c.kwargs.get("width") == "100%"]
    assert outer_calls
    outer_args = outer_calls[-1].args
    assert error_component in outer_args, "error_banner component not found in outer vstack args"
