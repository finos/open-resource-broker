"""Reflex configuration for the ORB UI (packaged copy).

This file ships inside the ``orb`` wheel at ``orb/ui/rxconfig.py`` so that
``reflex run`` can find it when the package is installed from PyPI.  The
embedded-UI runtime (``run_embedded_foreground``) sets ``cwd`` to the
directory that contains this file before exec-ing ``reflex run``.

A thin re-export at the repository root (``rxconfig.py``) delegates here so
that ``reflex run`` from the repo root continues to work for local
development.
"""

import os

import reflex as rx

# Note: pass --no-ssr when running ``reflex run`` / ``reflex export``.
# The vaul drawer used in detail panels reads ``document`` at module
# scope and crashes the React-Router node-side prerender.  ``orb server
# start`` already passes this flag automatically when ``ui.enabled``.

# Toggle UI features via env so the same config file works for embedded
# and remote modes.
_ORB_UI_BACKEND_PORT = int(os.getenv("ORB_UI_BACKEND_PORT", "8001"))
_ORB_UI_FRONTEND_PORT = int(os.getenv("ORB_UI_FRONTEND_PORT", "3000"))

config = rx.Config(
    # Reflex resolves the app via this dotted module path; it imports
    # ``orb.ui.app`` and looks for a top-level ``app`` (rx.App instance).
    # ``app_name`` must match ^[a-zA-Z][a-zA-Z0-9_]*$ (Reflex requirement).
    # The actual dotted import path is ``app_module_import`` below.
    app_name="orb_ui",
    app_module_import="orb.ui.app",
    backend_port=_ORB_UI_BACKEND_PORT,
    frontend_port=_ORB_UI_FRONTEND_PORT,
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(appearance="light", accent_color="blue", radius="medium"),
        ),
    ],
)
