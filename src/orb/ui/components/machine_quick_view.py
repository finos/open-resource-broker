"""Read-only machine detail drawer for embedding outside the Machines page.

The Machines page owns its own ``MachinesState.open_drawer`` flow that
mutates per-page selection state. The Requests page (and any other
caller that wants to drill into a single machine without leaving its
own page) uses this lightweight state instead so the two drawers do
not fight over a single ``selected_machine`` field.

The drawer renders via the shared ``machine_drawer`` component, which
reads the same set of fields (``selected_machine``, ``drawer_open``,
``syncing_drawer``, ``last_sync_time``, ``sync_error``) regardless of
which state class owns them.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
from datetime import datetime, timezone
from typing import Any

import reflex as rx

from .. import api


def _fmt_unix_ts(ts: int | str | None) -> str:
    """Format a unix-seconds int or ISO-8601 string as ``YYYY-MM-DD HH:MM UTC``."""
    if ts is None:
        return "—"
    try:
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            ms = ts if ts >= 1e12 else ts * 1000
            dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)


_EMPTY_MACHINE: dict[str, Any] = {
    "machine_id": "",
    "name": "",
    "status": "",
    "instance_type": "",
    "private_ip": "",
    "public_ip": None,
    "result": "",
    "launch_time": None,
    "message": "",
    "provider_api": None,
    "provider_name": None,
    "provider_type": None,
    "resource_id": None,
    "request_id": None,
    "return_request_id": None,
    "cloud_host_id": None,
    "price_type": None,
    "private_dns_name": None,
    "public_dns_name": None,
    "metadata": None,
    "health_checks": None,
    "template_id": None,
    "image_id": None,
    "subnet_id": None,
    "security_group_ids": [],
    "status_reason": None,
    "termination_time": None,
    "tags": None,
    "provider_data": {},
    "version": 0,
    "region": None,
    "availability_zone": None,
    "vcpus": None,
}


class MachineQuickViewState(rx.State):
    """Standalone drawer state for inspecting a single machine.

    Used by the request drawer to let users drill from a machine
    reference into the full machine view without navigating away.
    """

    drawer_open: bool = False
    selected_machine: dict[str, Any] = _EMPTY_MACHINE
    syncing_drawer: bool = False
    last_sync_time: str = ""
    sync_error: str = ""

    # Drawer live-poll toggle (default off — machine state changes less often)
    live_poll_enabled: str = rx.LocalStorage("false", name="orb-machine-drawer-live")

    # Pre-formatted computed vars required by the shared ``machine_drawer``
    # component. Mirroring the surface area of ``MachinesState`` so the
    # same drawer template renders against either state class.

    @rx.var
    def selected_machine_launch_fmt(self) -> str:
        return _fmt_unix_ts(self.selected_machine.get("launch_time"))

    @rx.var
    def selected_machine_term_fmt(self) -> str:
        return _fmt_unix_ts(self.selected_machine.get("termination_time"))

    @rx.var
    def selected_machine_tags_text(self) -> str:
        tags = self.selected_machine.get("tags")
        if not tags:
            return "{}"
        try:
            return json.dumps(tags, indent=2)
        except Exception:
            return str(tags)

    @rx.var
    def selected_machine_health_text(self) -> str:
        hc = self.selected_machine.get("health_checks")
        if hc is None:
            return "null"
        try:
            return json.dumps(hc, indent=2)
        except Exception:
            return str(hc)

    @rx.var
    def selected_machine_provider_data_text(self) -> str:
        pd = self.selected_machine.get("provider_data")
        if not pd:
            return "{}"
        try:
            return json.dumps(pd, indent=2)
        except Exception:
            return str(pd)

    @rx.var
    def selected_machine_sg_text(self) -> str:
        sgs = self.selected_machine.get("security_group_ids") or []
        return ", ".join(str(x) for x in sgs) if sgs else "—"

    @rx.event
    def toggle_live_poll(self, checked: bool) -> None:
        """Toggle the machine drawer live-poll on/off. Stored as 'true'/'false' for LocalStorage."""
        self.live_poll_enabled = "true" if checked else "false"

    @rx.event(background=True)
    async def poll_drawer_machine(self) -> None:
        """Poll the open machine every 3s until terminal or drawer closes.

        Respects ``live_poll_enabled``: when paused the loop sleeps briefly
        without hitting the API. Aborts when the drawer closes or the
        selected machine changes.
        """
        async with self:
            mid = str((self.selected_machine or {}).get("machine_id") or "")
            if not mid or not self.drawer_open:
                return
        while True:
            async with self:
                if not self.drawer_open:
                    return
                current = str((self.selected_machine or {}).get("machine_id") or "")
                if current != mid:
                    return
                paused = self.live_poll_enabled != "true"
            if paused:
                await asyncio.sleep(2)
                continue
            try:
                full = await api.get_machine(mid)
                if isinstance(full, dict):
                    if (
                        "machines" in full
                        and isinstance(full["machines"], list)
                        and full["machines"]
                    ):
                        full = full["machines"][0]
                    async with self:
                        # Skip writeback while a Sync click is in flight; the
                        # provider-side ``sync_machine`` result would otherwise
                        # be clobbered by this pure-read poll response.
                        if (
                            self.drawer_open
                            and str((self.selected_machine or {}).get("machine_id") or "") == mid
                            and not self.syncing_drawer
                        ):
                            self.selected_machine = {**_EMPTY_MACHINE, **full}
            except Exception:
                # API error during background poll — keep polling; drawer will show stale data
                pass
            async with self:
                status = str((self.selected_machine or {}).get("status") or "").lower()
            if status in ("terminated", "failed"):
                return
            await asyncio.sleep(3)

    @rx.event
    async def open_drawer(self, machine: dict[str, Any]):
        self.selected_machine = {**_EMPTY_MACHINE, **(machine or {})}
        self.drawer_open = True
        self.sync_error = ""
        machine_id = self.selected_machine.get("machine_id", "")
        if not machine_id:
            return
        self.syncing_drawer = True
        try:
            full = await api.get_machine(machine_id)
            if isinstance(full, dict):
                if "machines" in full and isinstance(full["machines"], list) and full["machines"]:
                    full = full["machines"][0]
                self.selected_machine = {**_EMPTY_MACHINE, **full}
        except Exception as exc:
            self.sync_error = f"Failed to load full machine details: {exc}"
        finally:
            self.syncing_drawer = False
        yield MachineQuickViewState.poll_drawer_machine

    @rx.event
    def close_drawer(self) -> None:
        self.drawer_open = False

    @rx.event
    def set_drawer_open(self, value: bool) -> None:
        self.drawer_open = value

    @rx.event
    async def sync_drawer_machine(self) -> None:
        """Refresh the open machine from the provider.

        Mirrors ``MachinesState.sync_drawer_machine`` but does not own
        the machines list — only the drawer view is updated.
        """
        machine_id = self.selected_machine.get("machine_id", "")
        if not machine_id:
            return
        self.syncing_drawer = True
        self.sync_error = ""
        try:
            payload = await api.sync_machine(machine_id)
            if payload.get("synced") is False and payload.get("sync_error"):
                self.sync_error = str(payload["sync_error"])
            refreshed = {**_EMPTY_MACHINE, **payload}
            refreshed.pop("synced", None)
            refreshed.pop("sync_error", None)
            self.selected_machine = refreshed
            self.last_sync_time = _dt.datetime.now().strftime("%H:%M:%S")
        except Exception as exc:
            self.sync_error = f"Sync failed: {exc}"
        finally:
            self.syncing_drawer = False

    # The drawer's return-from-here button is not wired in the quick
    # view; return flows live on the Machines page proper.
    @rx.event
    async def return_drawer_machine(self) -> None:
        self.drawer_open = False
