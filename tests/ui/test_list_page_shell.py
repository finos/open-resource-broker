"""Unit tests for the list_page_shell shared component.

The rx stub is already installed by conftest.py at collection time, so
all orb.ui imports are safe inside test functions.
"""

from __future__ import annotations


def test_list_page_shell_composes_without_raising() -> None:
    """list_page_shell must return a component when called with minimal args."""
    import reflex as rx

    from orb.ui.components.list_page_shell import list_page_shell

    # Minimal stand-ins — the rx stub accepts any call so we just need
    # to confirm the Python-level composition doesn't raise.
    component = list_page_shell(
        filter_row=rx.fragment(),
        toolbar=rx.fragment(),
        grid=rx.fragment(),
        load_more=rx.fragment(),
        empty=rx.fragment(),
        error_banner=rx.fragment(),
        is_loading=rx.Var.create(False),
        is_empty=rx.Var.create(True),
    )

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

    # banners and dialogs not passed — should default to []
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
