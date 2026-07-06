"""Status badge primitives for machines and requests.

Usage
-----
    from ..components.status_badge import machine_status_badge, request_status_badge

    # Inside a table row (status is an rx.Var[str]):
    machine_status_badge(machine["status"])

    # With a plain string (e.g. a hardcoded preview):
    request_status_badge("in_progress")

Both helpers use ``rx.match`` so they work correctly when *status* is a Reflex
Var evaluated at runtime, not just a plain Python string.
"""

from __future__ import annotations

import reflex as rx


def _aria_status(status: str | rx.Var) -> str | rx.Var:
    """Build an ``aria-label`` string for a status badge.

    Reflex's ``LiteralStringVar.__add__`` only accepts another ``StringVar``.
    Dict-subscript Vars (``row["status"]`` / ``machine["status"]``) compile
    to ``ObjectItemOperation`` — a generic Var with no concrete string type
    — and direct concatenation raises ``VarTypeError`` at compile time.

    For Var inputs we call ``.to(str)`` to coerce the operation into a typed
    string Var that the literal prefix will accept; for plain Python strings
    we fall back to a normal f-string.
    """
    if isinstance(status, rx.Var):
        return rx.Var.create("Status: ") + status.to(str)
    return f"Status: {status}"


def machine_status_badge(status: str | rx.Var) -> rx.Component:
    """Render a coloured badge for a machine status value.

    Color mapping (matches React PoC Badge.jsx / StatusDot.jsx):
      running / succeed / success → green
      pending / in_progress       → blue
      stopped                     → gray
      shutting-down               → orange
      terminated                  → gray
      failed / error              → red
      (default)                   → gray

    Args:
        status: The machine status string or a Reflex Var containing one.

    Returns:
        An ``rx.badge`` component styled by status.  An ``aria-label`` of the
        form "Status: <value>" is added so screen readers announce the meaning
        rather than just the badge colour.

    Example::

        machine_status_badge(machine["status"])
        machine_status_badge("running")  # renders green "running" badge

    Note:
        TODO(a11y-i18n): The "Status: " prefix is English-only.
    """
    # aria_label interpolation: for Var inputs Reflex evaluates the expression
    # at render time; for plain str it collapses to a static string.
    return rx.badge(
        status,
        color_scheme=rx.match(
            status,
            ("running", "green"),
            ("succeed", "green"),
            ("success", "green"),
            ("pending", "blue"),
            ("in_progress", "blue"),
            ("stopped", "gray"),
            ("terminated", "gray"),
            ("shutting-down", "orange"),
            ("failed", "red"),
            ("error", "red"),
            "gray",
        ),
        variant="soft",
        size="1",
        aria_label=_aria_status(status),
    )


_HF_STATUS_REVERSE_MAP: dict[str, str] = {
    # The REST API currently runs requests through the HostFactory response
    # formatter (orb.infrastructure.scheduler.hostfactory.response_formatter),
    # which collapses every non-success terminal state to "complete_with_error"
    # to satisfy Symphony HF's wire spec. That's correct for HF CLI consumers
    # but unhelpful for the UI — we want to see the real state. Reverse-map
    # the HF buckets back to the closest internal RequestStatus value.
    "complete_with_error": "partial",
}


def _resolve_display_status(status):
    """Map HF wire status back to the internal RequestStatus when possible."""
    if isinstance(status, str):
        return _HF_STATUS_REVERSE_MAP.get(status, status)
    # Reflex Var — use rx.match to do the mapping at render time.
    return rx.match(
        status,
        ("complete_with_error", "partial"),
        status,
    )


def request_status_badge(status: str | rx.Var) -> rx.Component:
    """Render a coloured badge for a request/allocation status value.

    Color mapping (matches React PoC Badge.jsx):
      complete / completed → green
      success / succeed    → green
      failed / fail        → red
      timeout / error      → red
      in_progress          → blue
      pending              → blue
      partial              → green
      cancelled            → gray
      (default)            → gray

    Args:
        status: The request status string or a Reflex Var containing one.

    Returns:
        An ``rx.badge`` component styled by status.  An ``aria-label`` of the
        form "Status: <value>" is added so screen readers announce the meaning
        rather than just the badge colour.

    Example::

        request_status_badge(request["status"])
        request_status_badge("in_progress")  # renders blue "in_progress" badge

    Note:
        TODO(a11y-i18n): The "Status: " prefix is English-only.
    """
    display_status = _resolve_display_status(status)
    return rx.badge(
        display_status,
        color_scheme=rx.match(
            display_status,
            ("complete", "green"),
            ("completed", "green"),
            ("success", "green"),
            ("succeed", "green"),
            ("launched", "green"),
            ("healthy", "green"),
            ("failed", "red"),
            ("fail", "red"),
            ("timeout", "red"),
            ("error", "red"),
            ("unhealthy", "red"),
            ("in_progress", "blue"),
            ("pending", "blue"),
            ("partial", "amber"),
            ("degraded", "orange"),
            ("cancelled", "gray"),
            "gray",
        ),
        variant="soft",
        size="1",
        aria_label=_aria_status(status),
    )
