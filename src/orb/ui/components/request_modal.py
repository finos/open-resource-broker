"""Reusable Request-Machines modal.

Triggered from a Template card / drawer (or a Machine/Request "run again"
shortcut). Collects ``count`` and submits via ``api.request_machines``.

Use ``request_modal(state)`` where ``state`` exposes:

  - ``request_modal_open: bool``
  - ``request_modal_template_id: str``
  - ``request_modal_count: int``
  - ``request_modal_loading: bool``
  - ``request_modal_error: str``
  - event ``request_modal_set_count(value: str)``
  - event ``request_modal_close()``
  - event ``request_modal_submit()``

The state class wires the events and calls ``api.request_machines``.
A canonical implementation is provided as :func:`request_modal_state_mixin`
for state classes that don't already define these fields.
"""

from __future__ import annotations

from typing import Any

import reflex as rx

from .. import api


class RequestModalState(rx.State):
    """Standalone state for the Request-Machines modal.

    Pages spawn the modal by calling ``RequestModalState.open(template_id)``
    and embedding ``request_modal()`` in their tree.
    """

    open: bool = False
    template_id: str = ""
    count: str = "1"
    loading: bool = False
    error: str = ""
    last_request_id: str = ""

    # Picker mode: when opened without a preselected template_id we load
    # the available templates and let the user choose from a dropdown. When
    # opened from a template card / drawer we skip the picker entirely.
    picker_mode: bool = False
    available_templates: list[dict[str, Any]] = []
    templates_loading: bool = False

    @rx.event
    async def open_for(self, template_id: str) -> None:
        self.template_id = template_id
        self.count = "1"
        self.error = ""
        self.last_request_id = ""
        self.picker_mode = False
        self.open = True

    @rx.event
    async def open_picker(self) -> None:
        """Open the modal without a preselected template.

        Used by the page-level "New Request" button: the user picks a
        template from a dropdown populated from ``GET /templates``.
        """
        self.template_id = ""
        self.count = "1"
        self.error = ""
        self.last_request_id = ""
        self.picker_mode = True
        self.open = True
        self.templates_loading = True
        try:
            res = await api.list_templates()
            self.available_templates = res.get("templates", []) or []
            # Default to the first template if there is one and nothing was preselected.
            if self.available_templates and not self.template_id:
                self.template_id = str(self.available_templates[0].get("template_id") or "")
        except Exception as exc:
            self.error = f"Failed to load templates: {exc}"
        finally:
            self.templates_loading = False

    @rx.event
    def set_template_id(self, value: str) -> None:
        self.template_id = value

    @rx.var
    def template_options(self) -> list[dict[str, str]]:
        """Dropdown options for the picker — list of {label, value} dicts.

        The label is ``name`` when available, falling back to ``template_id``
        so the user sees a human-readable name in the dropdown rather than a
        raw ID slug.  The value is always ``template_id`` so submission is
        unaffected.
        """
        result: list[dict[str, str]] = []
        for t in self.available_templates:
            tid = str(t.get("template_id") or "")
            if not tid:
                continue
            name = str(t.get("name") or "").strip() or tid
            result.append({"label": name, "value": tid})
        return result

    @rx.event
    async def close(self) -> None:
        self.open = False
        self.error = ""

    @rx.event
    def dismiss_success_banner(self) -> None:
        """Hide the acquire success banner."""
        self.last_request_id = ""

    @rx.event
    def view_request(self) -> Any:
        """Dismiss banner + navigate to /requests in one click."""
        self.last_request_id = ""
        return rx.redirect("/requests")

    @rx.event
    async def set_count(self, value: str) -> None:
        self.count = value

    @rx.event(background=True)
    async def submit(self) -> Any:
        """Submit the request without blocking the UI.

        ``@rx.event`` (foreground) holds the state lock for the duration
        of the handler — the ``loading=True`` mutation only flushes to the
        frontend after the long ``api.request_machines`` await returns, so
        the user never sees the button gray out. Switching to
        ``background=True`` lets us flush ``loading=True`` to the UI
        immediately via the ``async with self:`` block, await the API
        call OUTSIDE the lock so other state events keep firing, then
        flush the success/error state atomically.
        """
        # Snapshot inputs + set loading=True under a short lock, then
        # release before the long network round-trip.
        async with self:
            if self.loading:
                return None  # re-entrancy guard
            try:
                n = int(self.count)
            except (TypeError, ValueError):
                self.error = "Count must be a positive integer."
                return None
            if n < 1:
                self.error = "Count must be at least 1."
                return None
            self.loading = True
            self.error = ""
            template_id = self.template_id

        # Long-running call outside the lock — UI stays responsive,
        # button shows loading=True, drawer/list polls keep working.
        try:
            result = await api.request_machines({"template_id": template_id, "count": n})
        except Exception as exc:
            async with self:
                self.error = f"Request failed: {exc}"
                self.loading = False
            return None

        # Success — close modal, set last_request_id for the success
        # banner rendered alongside request_modal() on every page. NO
        # redirect — operators stay in context and click the banner link
        # if they want to drill in. Mirrors the return-machines flow so
        # both acquire + return give consistent feedback. Scroll to
        # the banner so it is in view even if the user was scrolled
        # mid-page when they submitted.
        async with self:
            self.last_request_id = str(result.get("request_id") or "")
            self.open = False
            self.loading = False
        return rx.call_script(
            "document.getElementById('request-success-banner')"
            "?.scrollIntoView({behavior: 'smooth', block: 'start'});"
        )


def request_success_banner() -> rx.Component:
    """Inline success banner shown after a request is submitted.

    Mounted alongside ``request_modal()`` on machines / templates pages so
    the operator gets the same kind of in-context feedback the return
    flow gives. Dismissable; auto-clears on next submit.
    """
    return rx.cond(
        RequestModalState.last_request_id != "",
        rx.callout.root(
            rx.hstack(
                rx.callout.icon(rx.icon("check-circle-2", size=16)),
                rx.callout.text(
                    "Request submitted: ",
                    rx.code(RequestModalState.last_request_id, size="1"),
                    rx.text(" — ", as_="span", color=rx.color("gray", 11)),
                    rx.link(
                        "View in Requests",
                        href="#",
                        on_click=RequestModalState.view_request,
                        color=rx.color("blue", 11),
                        underline="hover",
                        cursor="pointer",
                    ),
                    size="2",
                ),
                rx.spacer(),
                # X lives INSIDE the callout so the dismissable affordance is
                # visually tied to the success block, not a stray icon to the
                # right of it.
                rx.icon_button(
                    rx.icon("x", size=14),
                    on_click=RequestModalState.dismiss_success_banner,
                    variant="ghost",
                    color_scheme="gray",
                    size="1",
                    aria_label="Dismiss success banner",
                ),
                spacing="2",
                align="center",
                width="100%",
            ),
            color_scheme="green",
            width="100%",
            margin_bottom="1rem",
            # Scroll anchor — submit handler emits a JS scroll to this id so
            # the banner is in view even when the user submits from a long
            # scrolled page.
            id="request-success-banner",
        ),
        rx.fragment(),
    )


def request_modal() -> rx.Component:
    """Render the modal. Mounted once per page."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.hstack(
                rx.heading("Request Machines", size="5"),
                rx.spacer(),
                rx.dialog.close(
                    rx.button(
                        rx.icon("x", size=16),
                        variant="ghost",
                        size="2",
                        on_click=RequestModalState.close,
                    )
                ),
                width="100%",
                align="center",
                margin_bottom="0.75rem",
            ),
            rx.divider(margin_bottom="1rem"),
            rx.vstack(
                rx.text("Template", size="2", color=rx.color("gray", 11)),
                rx.cond(
                    RequestModalState.picker_mode,
                    rx.cond(
                        RequestModalState.templates_loading,
                        rx.hstack(
                            rx.spinner(size="1"),
                            rx.text("Loading templates…", size="2", color=rx.color("gray", 10)),
                            spacing="2",
                            align="center",
                        ),
                        rx.select.root(
                            rx.select.trigger(
                                placeholder="Choose a template…",
                                width="100%",
                            ),
                            rx.select.content(
                                rx.foreach(
                                    RequestModalState.template_options,
                                    lambda opt: rx.select.item(
                                        opt["label"],
                                        value=opt["value"],
                                    ),
                                ),
                            ),
                            value=RequestModalState.template_id,
                            on_change=RequestModalState.set_template_id,
                            width="100%",
                        ),
                    ),
                    rx.code(
                        RequestModalState.template_id,
                        size="2",
                        word_break="break-all",
                    ),
                ),
                rx.box(height="0.75rem"),
                rx.text("Count", size="2", color=rx.color("gray", 11)),
                rx.input(
                    value=RequestModalState.count,
                    on_change=RequestModalState.set_count,
                    type="number",
                    placeholder="1",
                    width="100%",
                ),
                rx.text(
                    "Number of machines to provision from this template.",
                    size="1",
                    color=rx.color("gray", 10),
                ),
                rx.cond(
                    RequestModalState.error != "",
                    rx.callout(
                        RequestModalState.error,
                        icon="triangle_alert",
                        color_scheme="red",
                        size="1",
                        margin_top="0.5rem",
                    ),
                    rx.fragment(),
                ),
                spacing="1",
                align="start",
                width="100%",
            ),
            rx.hstack(
                rx.spacer(),
                rx.button(
                    "Cancel",
                    variant="soft",
                    color_scheme="gray",
                    on_click=RequestModalState.close,
                ),
                rx.button(
                    rx.icon("send", size=14),
                    "Submit Request",
                    on_click=RequestModalState.submit,
                    loading=RequestModalState.loading,
                    color_scheme="blue",
                ),
                spacing="2",
                width="100%",
                margin_top="1.5rem",
            ),
            max_width="480px",
        ),
        open=RequestModalState.open,
    )
