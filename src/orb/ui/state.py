"""Global app state — shared shell concerns (health, nav)."""

from __future__ import annotations

from typing import Any

import httpx
import reflex as rx

from . import api


class AppState(rx.State):
    """Top-level state for shell (health badge, server info)."""

    health: dict[str, Any] = {}
    info: dict[str, Any] = {}
    health_error: str = ""

    # Provider column schemas — fetched once on mount and shared across all
    # list pages.  Shape: ``{provider_name: [column_descriptor_dict, ...]}``.
    # Populated by ``load_provider_schemas``; defaults to empty dict so pages
    # render with base columns only until the fetch completes.
    provider_schemas: dict[str, list[dict[str, Any]]] = {}
    _schemas_loaded: bool = False

    @rx.event(background=True)
    async def load_provider_schemas(self):
        """Fetch provider column schemas once and cache in state.

        Single-flight: subsequent calls while schemas are already loaded
        are no-ops.  Schema data is stable for the lifetime of a server
        process so re-fetching is not required.
        """
        async with self:
            if self._schemas_loaded:
                return
            self._schemas_loaded = True
        try:
            schemas = await api.get_provider_schemas()
            async with self:
                self.provider_schemas = schemas if isinstance(schemas, dict) else {}
        except Exception:
            async with self:
                self.provider_schemas = {}

    # Sidebar collapse state — persisted in LocalStorage so it survives
    # page navigation and hard reloads. Stored as a string ("true"/"false")
    # because LocalStorage values are always strings.
    sidebar_collapsed: str = rx.LocalStorage("false", name="orb-sidebar-collapsed")

    # Onboarding banner dismissal — same storage strategy, per-browser flag.
    _onboarding_dismissed_raw: str = rx.LocalStorage("false", name="orb-onboarding-dismissed")

    @rx.var
    def is_collapsed(self) -> bool:
        """True when the sidebar is in icon-only rail mode."""
        return self.sidebar_collapsed == "true"

    @rx.var
    def onboarding_dismissed(self) -> bool:
        """True when the user has hidden the dashboard onboarding banner."""
        return self._onboarding_dismissed_raw == "true"

    @rx.event
    def toggle_sidebar(self):
        """Flip sidebar between expanded (240px) and collapsed (64px) rail."""
        self.sidebar_collapsed = "false" if self.is_collapsed else "true"

    @rx.event
    def dismiss_onboarding(self):
        """Hide the ``Get started with ORB`` banner on the dashboard.

        Persisted in LocalStorage so the banner stays hidden across
        reloads even if the templates count is later corrected to zero.
        """
        self._onboarding_dismissed_raw = "true"

    # Guard so multiple page mounts don't each spawn their own background
    # poll loop. Reflex fires ``on_mount`` on every page entry, and the
    # ``page()`` helper attaches ``poll_health`` to it. Without this guard
    # we end up with N concurrent loops all writing to ``health`` every
    # 15s, causing visible status flicker and websocket churn.
    _poll_started: bool = False

    async def _poll_health_tick(self) -> None:
        """Execute a single health-poll iteration: fetch health + info, update state.

        Extracted from the ``poll_health`` background loop so tests can call it
        directly without needing to run the full infinite-loop coroutine.
        The ``async with self:`` block is retained so the method works correctly
        whether called in a test (with the no-op CM subclass) or inside the real
        Reflex event loop.
        """
        async with self:
            try:
                self.health = await api.get_health()
                self.info = await api.get_info()
                self.health_error = ""
            except httpx.HTTPError as e:
                self.health_error = str(e)
            except Exception as e:
                self.health_error = str(e)

    @rx.event(background=True)
    async def poll_health(self):
        # Single-flight guard so re-mounting a page doesn't spawn a
        # second concurrent loop writing to the same state.
        async with self:
            if self._poll_started:
                return
            self._poll_started = True
        try:
            import asyncio

            while True:
                await self._poll_health_tick()
                await asyncio.sleep(15)
        finally:
            async with self:
                self._poll_started = False

    @rx.var
    def server_status(self) -> str:
        if self.health_error:
            return "offline"
        return self.health.get("status", "unknown")

    @rx.var
    def server_status_color(self) -> str:
        s = self.server_status
        if s in ("ok", "healthy"):
            return "green"
        if s == "unhealthy":
            return "red"
        return "gray"

    @rx.var
    def health_check_rows(self) -> list[dict[str, str]]:
        """Pre-formatted per-component health rows for the Config page.

        The /health endpoint returns nested dicts keyed by component name.
        Reflex Vars cannot index untyped dict-of-dict at template time, so
        we surface a flat list typed as ``list[dict[str, str]]`` and the
        page just iterates over it.
        """
        checks = self.health.get("checks") if isinstance(self.health, dict) else None
        if not isinstance(checks, dict):
            return []
        rows: list[dict[str, str]] = []
        for name in sorted(checks.keys()):
            detail = checks.get(name)
            status = "unknown"
            message = ""
            if isinstance(detail, dict):
                status = str(detail.get("status") or "unknown")
                message = str(detail.get("message") or "")
            rows.append({"name": name, "status": status, "message": message})
        return rows


class CurrentUserState(rx.State):
    """Current authenticated user — role, permissions, and convenience vars.

    Pages should call ``CurrentUserState.load`` on mount via the page() helper
    (in components/layout.py) alongside ``AppState.poll_health``.  Do not wire
    that call automatically here; another phase owns the layout integration.
    """

    username: str = ""
    role: str = "viewer"
    permissions: list[str] = []
    loaded: bool = False

    @rx.event
    async def load(self):
        """Fetch /me from ORB and populate user fields.

        Degrades gracefully: if the endpoint does not exist yet (404) the
        http layer returns a default admin payload.  Any other exception
        falls back to an anonymous viewer so the UI still renders.
        """
        try:
            data = await api.get_me()
            self.username = data.get("username", "")
            self.role = data.get("role", "viewer")
            self.permissions = data.get("permissions", [])
        except Exception:
            self.username = "anonymous"
            self.role = "viewer"
            self.permissions = []
        self.loaded = True

    # --- Role helpers -------------------------------------------------------

    @rx.var
    def is_viewer(self) -> bool:
        return self.role == "viewer"

    @rx.var
    def is_operator(self) -> bool:
        return self.role in {"operator", "admin"}

    @rx.var
    def is_admin(self) -> bool:
        return self.role == "admin"

    # --- Permission helpers -------------------------------------------------

    @rx.var
    def can_request_machines(self) -> bool:
        return "request_machines" in self.permissions

    @rx.var
    def can_return_machines(self) -> bool:
        return "return_machines" in self.permissions

    @rx.var
    def can_cancel_request(self) -> bool:
        return "cancel_request" in self.permissions

    @rx.var
    def can_create_template(self) -> bool:
        return "create_template" in self.permissions

    @rx.var
    def can_update_template(self) -> bool:
        return "update_template" in self.permissions

    @rx.var
    def can_delete_template(self) -> bool:
        return "delete_template" in self.permissions
