"""Error callout banner component.

Renders a red Radix callout with a triangle-alert icon, the error message, and
an optional Retry button.  Mirrors the React PoC's ErrorBanner.jsx.

Design decision: this component is *always* rendered (no internal ``rx.cond``).
The caller is responsible for wrapping it in ``rx.cond`` if the error may be
absent.  This keeps the component predictable and avoids Var-type surprises.

Usage
-----
    from ..components.error_callout import error_callout

    # Conditional display — show only when there is an error:
    rx.cond(
        MyState.error != "",
        error_callout(MyState.error, retry=MyState.load),
        rx.fragment(),
    )

    # Always-present (e.g. a static error in a detail view):
    error_callout("Something went wrong")
"""

from __future__ import annotations

from typing import Any, Optional

import reflex as rx


def error_callout(
    error: rx.Var | str,
    retry: Optional[Any] = None,
) -> rx.Component:
    """Return a red callout displaying *error* with an optional Retry button.

    Args:
        error: The error message to display.  Can be a plain ``str`` or a
               ``rx.Var[str]``.  The component does NOT guard against empty
               strings — wrap in ``rx.cond`` at the call site when needed.
        retry: An optional event handler attached to a "Retry" button.  When
               ``None``, no button is rendered.

    Returns:
        An ``rx.callout`` (Radix) component with red color scheme and a
        ``triangle-alert`` icon.

    Example::

        # In a page, show the callout only when error is non-empty:
        rx.cond(
            MachinesState.error != "",
            error_callout(
                MachinesState.error,
                retry=MachinesState.load,
            ),
            rx.fragment(),
        )
    """
    inner_children: list[rx.Component] = [
        rx.callout.text(error),
    ]

    if retry is not None:
        inner_children.append(
            rx.button(
                "Retry",
                size="1",
                variant="soft",
                color_scheme="red",
                on_click=retry,
                margin_top="0.5rem",
            )
        )

    # role="alert" + aria-live="assertive" cause screen readers to announce the
    # error immediately, interrupting any current speech — appropriate for error
    # messages that require immediate user attention.
    # The triangle-alert icon is decorative; the callout text conveys the error.
    return rx.callout.root(
        rx.callout.icon(
            rx.icon("triangle-alert", size=16, aria_hidden="true"),
        ),
        *inner_children,
        color_scheme="red",
        variant="soft",
        width="100%",
        role="alert",
        aria_live="assertive",
    )
