"""JSON viewer component for detail drawers.

Renders pre-formatted JSON in a scrollable, monospace code block.  Used in
drawer panels for tags, configuration, provider_data, metadata, health_checks.

Design decision: because Reflex Var-to-string conversion for arbitrary dicts
is not available at the template layer in Reflex 0.9, the recommended pattern
is to pre-format JSON in a ``@rx.var`` computed property on the State (already
done in machines.py for all drawer fields), then pass the resulting ``str``
Var here.  Passing a plain Python ``dict`` is also supported for static views.

Usage
-----
    from ..components.json_view import json_view

    # With a pre-formatted str Var (preferred — no runtime serialisation):
    json_view(MachinesState.selected_machine_tags_text)

    # With a plain Python dict (static / non-reactive use only):
    import json as _json
    json_view(_json.dumps({"key": "value"}, indent=2))
"""

from __future__ import annotations

import json as _json

import reflex as rx


def json_view(
    data: rx.Var | dict | str,
    max_height: str = "400px",
) -> rx.Component:
    """Render *data* as a scrollable, syntax-highlighted JSON code block.

    For reactive Vars the caller should provide a pre-serialised ``str`` Var
    (from an ``@rx.var`` computed property).  Raw ``dict`` values are
    serialised with ``json.dumps`` at component-build time (Python side, not
    reactive).

    Args:
        data:       A ``rx.Var[str]`` containing pre-formatted JSON, a plain
                    Python ``dict``, or a raw ``str`` of JSON.
        max_height: CSS max-height for the scrollable container (default
                    ``"400px"``).

    Returns:
        A scrollable ``rx.box`` containing an ``rx.code`` block with monospace
        font and a subtle background — consistent with the ORB shell's Radix
        Themes styling.

    Note:
        ``rx.code_block`` with ``language="json"`` provides syntax
        highlighting but requires the ``pygments`` extra; it is used here when
        *data* is a plain str/dict (static).  For reactive Vars we fall back to
        ``rx.code`` inside a styled box to avoid serialisation issues.

    Example::

        # In a detail drawer, using a pre-computed str Var:
        json_view(MachinesState.selected_machine_tags_text)

        # Static preview:
        json_view({"region": "us-east-1", "vcpus": 8})
    """
    # --- Normalise static (non-Var) inputs at Python build time -------------
    if isinstance(data, dict):
        data = _json.dumps(data, indent=2, default=str)

    # For plain strings (either passed directly or just converted from dict)
    # we can use rx.code_block which supports syntax highlighting.
    # For Var inputs we use a plain rx.code inside a styled box.
    if isinstance(data, str):
        # tabindex="0" allows keyboard users to focus the region and scroll it
        # with arrow keys.  aria-label identifies the region to screen readers.
        # TODO(a11y-i18n): "JSON content" label is English-only.
        return rx.box(
            rx.code_block(
                data,
                language="json",
                wrap_long_lines=True,
                width="100%",
            ),
            overflow_y="auto",
            max_height=max_height,
            border_radius="0.375rem",
            width="100%",
            aria_label="JSON content",
            tab_index=0,
        )

    # data is an rx.Var — render as monospace code block
    # tabindex="0" + aria-label mirror the static branch above.
    return rx.box(
        rx.box(
            rx.code(
                data,
                white_space="pre",
                font_family="monospace",
                font_size="0.75rem",
                display="block",
            ),
            padding="0.75rem",
            background=rx.color("gray", 2),
            border=f"1px solid {rx.color('gray', 5)}",
            border_radius="0.375rem",
            overflow_x="auto",
            width="100%",
        ),
        overflow_y="auto",
        max_height=max_height,
        width="100%",
        aria_label="JSON content",
        tab_index=0,
    )
