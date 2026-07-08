"""Auto-refresh control widget.

Renders: [Refresh] [checkbox: Auto-refresh] [select: interval] [Last updated text]

Usage
-----
    from ..components.refresh_control import refresh_control

    refresh_control(
        enabled=MyState.auto_refresh_enabled,
        interval=MyState.auto_refresh_interval,
        on_toggle=MyState.toggle_auto_refresh,
        on_set_interval=MyState.set_auto_refresh_interval,
        on_manual_refresh=MyState.load,
        last_refresh_text=MyState.last_refresh,
    )

Each state that embeds this widget must declare:

    auto_refresh_enabled: str = rx.LocalStorage("true", name="orb-<page>-auto-refresh-enabled")
    auto_refresh_interval: str = rx.LocalStorage("10", name="orb-<page>-auto-refresh-interval")

    @rx.event
    def set_auto_refresh_enabled(self, value: str):
        self.auto_refresh_enabled = value

    @rx.event
    def set_auto_refresh_interval(self, value: str):
        self.auto_refresh_interval = value

    @rx.event
    def toggle_auto_refresh(self, checked: bool):
        self.auto_refresh_enabled = "true" if checked else "false"
"""

from __future__ import annotations

from typing import Optional

import reflex as rx

_INTERVAL_OPTIONS: list[tuple[str, str]] = [
    ("5", "5 sec"),
    ("10", "10 sec"),
    ("30", "30 sec"),
    ("60", "60 sec"),
]


def refresh_control(
    enabled: rx.Var[str],
    interval: rx.Var[str],
    on_toggle: rx.EventHandler,
    on_set_interval: rx.EventHandler,
    on_manual_refresh: rx.EventHandler,
    last_refresh_text: rx.Var[str],
    loading: Optional[rx.Var[bool]] = None,
) -> rx.Component:
    """Compact auto-refresh widget placed in page toolbars.

    Parameters
    ----------
    enabled:
        String Var — ``"true"`` or ``"false"``. Backed by LocalStorage.
    interval:
        String Var — one of ``"5" | "10" | "30" | "60"``. Backed by LocalStorage.
    on_toggle:
        Event handler receiving ``bool`` — adapter writes ``"true"/"false"``
        back to *enabled*.  Signature: ``toggle_auto_refresh(checked: bool)``.
    on_set_interval:
        Event handler receiving ``str`` — the newly-selected interval string.
    on_manual_refresh:
        Event handler for the manual Refresh button (no arguments).
    last_refresh_text:
        String Var shown as ``"Last updated: HH:MM:SS"``.
    loading:
        Optional bool Var wired to the Refresh button's ``loading`` prop.
    """
    refresh_btn = rx.button(
        rx.icon("refresh-cw", size=14),
        "Refresh",
        on_click=on_manual_refresh,
        loading=loading if loading is not None else False,
        variant="soft",
        size="2",
    )

    auto_checkbox = rx.checkbox(
        checked=enabled == "true",
        on_change=on_toggle,
        size="2",
    )

    interval_select = rx.select.root(
        rx.select.trigger(placeholder="10 sec"),
        rx.select.content(
            *[rx.select.item(label, value=val) for val, label in _INTERVAL_OPTIONS],
        ),
        value=interval,
        on_change=on_set_interval,
        size="2",
        disabled=enabled != "true",
    )

    last_updated = rx.cond(
        last_refresh_text != "",
        rx.text(
            "Last updated: ",
            rx.text.span(last_refresh_text, font_weight="500"),
            size="2",
            color=rx.color("gray", 10),
        ),
        rx.fragment(),
    )

    return rx.hstack(
        refresh_btn,
        rx.hstack(
            auto_checkbox,
            rx.text("Auto-refresh", size="2", color=rx.color("gray", 11)),
            spacing="1",
            align="center",
        ),
        interval_select,
        last_updated,
        spacing="2",
        align="center",
    )
