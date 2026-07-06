"""Naive fixed-height virtualised list for Reflex 0.9.

IMPORTANT — PRODUCTION NOTE
============================
This is a *smart-cap* implementation — it renders all items inside a
``overflow_y="auto"`` scrollable container and relies on browser-native
scroll rendering.  It does NOT remove off-screen DOM nodes (not true DOM
virtualisation).

True virtual DOM virtualisation (mounting only visible rows) requires a
custom React component wrapping ``react-window`` or ``@tanstack/react-virtual``.
Neither library ships in any ``reflex_components_*`` package in this
environment, so adding one would require an npm dependency.

Future work — upgrade to real virtualisation without changing the public API
----------------------------------------------------------------------------
Step 1: add npm dep (once approved)::

    # rxconfig.py:
    app = rx.App(
        stylesheets=[],
        # or via package.json:  "react-window": "^1.8"
    )

Step 2: swap inner implementation::

    class _FixedSizeList(rx.NoSSRComponent):
        library = "react-window"
        tag = "FixedSizeList"
        item_count: rx.Var[int]
        item_size: rx.Var[int]   # = item_height
        height: rx.Var[int]

Step 3: ``virtualized_list()`` public signature stays identical — pages
require zero changes.

What this implementation provides
----------------------------------
*   Scrollable ``rx.box`` with ``overflow_y="auto"`` and a stable HTML ``id``.
*   ``on_scroll`` (Reflex 0.9 no-arg DOM trigger) + ``rx.call_script`` reads
    ``scrollTop`` and updates ``VirtualizedListState``.
*   "Near bottom" detection: a second ``call_script`` reads a boolean from JS
    and stores it in ``VirtualizedListState.near_bottom[container_id]``.
*   ``rx.cond`` on that reactive boolean fires ``on_load_more`` — enabling
    server-driven infinite scroll with zero custom JS hooks.
*   Small-list fast path: lists with ≤ ``_SMALL_LIST_THRESHOLD`` items skip
    scroll-tracking overhead entirely.

Limitations
-----------
*   All items remain in the DOM.  For > 500 rows with complex row components
    use server-side pagination so the rendered slice stays small.
*   ``item_height`` is accepted for API compatibility (callers use it to size
    their containers consistently) but is not used for DOM spacer calculations
    in this impl.
*   Scroll position is reset on page navigation.
*   ``on_scroll`` fires on every scroll pixel — Reflex's event batching
    reduces server round-trips, but a heavy state handler will still lag.
    Keep ``set_scroll_top`` / ``set_near_bottom`` lightweight (they are).
"""

from __future__ import annotations

from typing import Any, Callable

import reflex as rx

# ── tunables ──────────────────────────────────────────────────────────────────

# Lists at or below this count skip scroll-state tracking.
_SMALL_LIST_THRESHOLD = 50

# Trigger on_load_more when within this many pixels of the bottom.
_LOAD_MORE_THRESHOLD_PX = 200


# ── state ─────────────────────────────────────────────────────────────────────


class VirtualizedListState(rx.State):
    """Per-container scroll and near-bottom state.

    Keyed by ``container_id`` so multiple lists on the same page remain
    independent.  Both dicts use ``container_id`` as the key.
    """

    # container_id -> scrollTop in px.  Exposed so pages can react if needed.
    scroll_positions: dict[str, float] = {}

    # container_id -> True when user is within _LOAD_MORE_THRESHOLD_PX of the
    # bottom.  Driven by the JS near-bottom check on each scroll event.
    near_bottom: dict[str, bool] = {}

    def set_scroll_top(self, container_id: str, scroll_top: float) -> None:
        """Store scroll position for *container_id*.

        Args:
            container_id: The HTML ``id`` of the scrollable wrapper.
            scroll_top:   Current ``element.scrollTop`` value in pixels.
        """
        self.scroll_positions = {
            **self.scroll_positions,
            container_id: float(scroll_top),
        }

    def set_near_bottom(self, container_id: str, near: bool) -> None:
        """Store near-bottom detection result for *container_id*.

        Args:
            container_id: The HTML ``id`` of the scrollable wrapper.
            near:         ``True`` when ``scrollTop + clientHeight >=
                          scrollHeight - threshold``.
        """
        self.near_bottom = {
            **self.near_bottom,
            container_id: near,
        }


# ── private helpers ────────────────────────────────────────────────────────────


def _scroll_top_js(container_id: str) -> str:
    """JS expression evaluating to the element's current ``scrollTop``."""
    return f"document.getElementById('{container_id}')?.scrollTop ?? 0"


def _near_bottom_js(container_id: str, threshold_px: int = _LOAD_MORE_THRESHOLD_PX) -> str:
    """JS expression evaluating to ``true`` when near the scroll bottom."""
    return (
        f"(function(){{"
        f"  var el=document.getElementById('{container_id}');"
        f"  if(!el) return false;"
        f"  return (el.scrollTop+el.clientHeight)>=(el.scrollHeight-{threshold_px});"
        f"}})()"
    )


# ── public component ──────────────────────────────────────────────────────────


def virtualized_list(
    items: rx.Var | list[dict],
    render_item: Callable[[Any], rx.Component],
    *,
    item_height: int = 56,
    viewport_height: str = "70vh",
    on_load_more: Any = None,
    estimated_total: int | None = None,
    key_fn: Callable[[dict], str] | None = None,
    container_id: str = "vlist-default",
) -> rx.Component:
    """Render *items* in a scrollable container with optional load-more.

    Args:
        items:            Full list of row dicts (a server-paginated slice).
                          Pass an ``rx.Var`` (reactive) or a plain Python list.
        render_item:      ``(item: dict) -> rx.Component``.  Passed directly
                          to ``rx.foreach`` — receives a single item dict.
        item_height:      Fixed row height in pixels.  Accepted for API
                          compatibility and future spacer calculations.
                          Default: 56.
        viewport_height:  CSS height string for the scrollable wrapper.
                          Default: ``"70vh"``.
        on_load_more:     ``rx.EventHandler`` (or ``EventSpec``) fired when
                          the user scrolls within 200 px of the bottom.
                          Pass ``None`` to disable infinite scroll.
        estimated_total:  Optional hint at the server-side total item count
                          (e.g. for a "showing N of M" label the caller renders
                          outside this component).  Not used internally.
        key_fn:           Accepted for API compatibility; not used in this
                          impl (``rx.foreach`` manages React keys).
        container_id:     HTML ``id`` for the scrollable wrapper.  Must be
                          unique per page when multiple lists coexist.
                          Default: ``"vlist-default"``.

    Returns:
        ``rx.box`` with ``overflow_y="auto"`` containing ``rx.vstack`` of all
        rendered items.

    Usage — machines page::

        from ..components import virtualized_list

        virtualized_list(
            items=MachinesState.filtered_machines,
            render_item=_machine_row,
            item_height=80,
            viewport_height="68vh",
            on_load_more=MachinesState.load_next_page,
            container_id="machines-vlist",
        )

    Usage — requests page::

        virtualized_list(
            items=RequestsState.request_rows,
            render_item=_request_row,
            item_height=56,
            viewport_height="70vh",
            on_load_more=RequestsState.load_next_page,
            container_id="requests-vlist",
        )

    Usage — templates page::

        virtualized_list(
            items=TemplatesState.template_rows,
            render_item=_template_row,
            item_height=72,
            viewport_height="70vh",
            on_load_more=TemplatesState.load_next_page,
            container_id="templates-vlist",
        )

    Multiple lists on the same page::

        # Each list must have a unique container_id.
        virtualized_list(..., container_id="left-list")
        virtualized_list(..., container_id="right-list")
    """
    # ── decide whether to attach scroll tracking ───────────────────────────────
    #
    # Small static lists: skip the scroll-state overhead.
    # Reactive Var lists: always attach (load-more may still be needed).
    is_small_static = isinstance(items, list) and len(items) <= _SMALL_LIST_THRESHOLD
    use_scroll = not is_small_static

    if not use_scroll:
        # ── fast path: tiny static list, no scroll tracking ───────────────────
        return rx.box(
            rx.vstack(
                rx.foreach(items, render_item),
                spacing="0",
                width="100%",
            ),
            id=container_id,
            overflow_y="auto",
            height=viewport_height,
            width="100%",
        )

    # ── build on_scroll event list ─────────────────────────────────────────────
    #
    # Reflex 0.9: ``on_scroll`` on any HTML element fires with no arguments.
    # ``rx.call_script(js, callback=fn)`` executes ``js`` on the client and
    # calls ``fn(result)`` — ``fn`` must be an EventHandler or lambda returning
    # one or more EventSpec values.
    #
    # A list of EventSpec values passed to on_scroll is the Reflex-idiomatic
    # way to fire multiple events from a single DOM trigger.

    scroll_events: list[Any] = [
        # 1. Update scroll position in state.
        rx.call_script(
            _scroll_top_js(container_id),
            callback=lambda st: VirtualizedListState.set_scroll_top(container_id, st),
        ),
    ]

    if on_load_more is not None:
        # 2. Write near-bottom boolean to state.
        scroll_events.append(
            rx.call_script(
                _near_bottom_js(container_id),
                callback=lambda near: VirtualizedListState.set_near_bottom(container_id, near),
            )
        )
        # 3. Reactively fire on_load_more when near_bottom[container_id] is True.
        #    rx.cond returns an EventCastedVar that Reflex accepts in event lists.
        nb_var: rx.Var = VirtualizedListState.near_bottom[container_id]  # type: ignore[index]
        scroll_events.append(rx.cond(nb_var, on_load_more, rx.noop()))

    # ── main container ─────────────────────────────────────────────────────────
    return rx.box(
        rx.vstack(
            rx.foreach(items, render_item),
            spacing="0",
            width="100%",
        ),
        id=container_id,
        overflow_y="auto",
        height=viewport_height,
        width="100%",
        on_scroll=scroll_events,
    )
