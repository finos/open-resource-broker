"""Accessible confirm dialog built on Radix ``rx.alert_dialog``.

The Radix AlertDialog provides a focus-trapped, accessible modal with ESC and
backdrop-click-to-dismiss built in — matching the React PoC's ConfirmModal
behaviour without requiring any custom keyboard handling.

Usage
-----
    from ..components.confirm_modal import confirm_modal

    # In the page component tree (rendered once, open_var controls visibility):
    confirm_modal(
        open_var=MachinesState.confirm_return_open,
        title="Return machines?",
        message="These machines will be returned to the pool. "
                "This action cannot be undone.",
        on_confirm=MachinesState.return_selected,
        on_cancel=MachinesState.close_confirm_return,
        danger=True,
    )

The dialog is hidden when *open_var* is False; no wrapper ``rx.cond`` needed.
"""

from __future__ import annotations

from typing import Any

import reflex as rx


def confirm_modal(
    open_var: rx.Var,
    title: str,
    message: str,
    on_confirm: Any,
    on_cancel: Any,
    danger: bool = False,
    confirm_label: str = "Confirm",
    cancel_label: str = "Cancel",
) -> rx.Component:
    """Return a Radix AlertDialog confirm modal component.

    The dialog is controlled via *open_var* — a ``bool`` Reflex Var on your
    State.  Pass the matching close handler as *on_cancel*.

    Args:
        open_var:       A ``rx.Var[bool]`` that controls dialog visibility.
        title:          Dialog heading text.
        message:        Body text explaining what will happen.
        on_confirm:     Event handler called when the confirm button is clicked.
        on_cancel:      Event handler called when Cancel or ESC is pressed.
        danger:         When True the confirm button uses a red color scheme
                        (destructive action pattern from the React PoC).
        confirm_label:  Label for the confirm button (default "Confirm").
        cancel_label:   Label for the cancel button (default "Cancel").

    Returns:
        An ``rx.alert_dialog.root`` component.  Include it anywhere in the
        component tree — it renders as a portal so position doesn't matter.

    Accessibility:
        ``rx.alert_dialog.root`` renders a ``<div role="alertdialog">``
        (Radix primitive).  Focus is automatically trapped inside the dialog
        while it is open and restored to the trigger element on close — no
        custom focus management code is required.  The Cancel button is the
        first focusable element, which is the safe default for destructive
        confirm dialogs.  ESC and backdrop-click invoke ``on_open_change``
        (wired to *on_cancel*) to close the dialog.

    Example::

        confirm_modal(
            open_var=MyState.confirm_open,
            title="Delete record?",
            message="This cannot be undone.",
            on_confirm=MyState.do_delete,
            on_cancel=MyState.cancel_delete,
            danger=True,
            confirm_label="Delete",
        )
    """
    confirm_color: str = "red" if danger else "blue"

    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title(title),
            rx.alert_dialog.description(message),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.button(
                        cancel_label,
                        variant="soft",
                        color_scheme="gray",
                        on_click=on_cancel,
                    ),
                ),
                rx.alert_dialog.action(
                    rx.button(
                        confirm_label,
                        color_scheme=confirm_color,
                        on_click=on_confirm,
                    ),
                ),
                spacing="3",
                justify="end",
                margin_top="1rem",
            ),
        ),
        open=open_var,
        on_open_change=on_cancel,
    )
