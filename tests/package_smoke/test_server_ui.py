"""``orb server start`` from the installed package, and the UI it serves.

The UI is driven over its websocket, the way the browser does, with the default
(disk) state manager: no state-manager setting is made anywhere. Each page's
load event must answer without an error in state, with rows keyed to a request
and machine this module creates through the REST API.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tests.package_smoke.conftest import (
    FLOW_TEMPLATE,
    SPOT_TEMPLATE,
    URL_LINE,
    Flow,
    Server,
    Smoke,
)

MIN_PAGES = 5

# Pages whose load shows stored data, and the page state they load.
DATA_PAGES: dict[str, str] = {
    "/": "orb.ui.pages.dashboard:DashboardState",
    "/machines": "orb.ui.pages.machines:MachinesState",
    "/requests": "orb.ui.pages.requests:RequestsState",
    "/templates": "orb.ui.pages.templates:TemplatesState",
}
# Pages with no data of their own to key to a seed; only "no error" is checked.
STATIC_PAGES: dict[str, str] = {
    "/config": "orb.ui.pages.config:ConfigState",
}


def _rest(server: Server, method: str, path: str, body: dict[str, Any] | None = None):
    request = urllib.request.Request(  # noqa: S310 - loopback
        f"{server.api}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {server.token}",
            "Content-Type": "application/json",
            "X-ORB-Scheduler": "default",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


@pytest.fixture(scope="module")
def seeded(server: Server) -> dict[str, str]:
    """A request made through the REST API and the machine it started."""
    status, created = _rest(
        server, "POST", "/api/v1/machines/request", {"template_id": FLOW_TEMPLATE, "count": 1}
    )
    assert status in (200, 201, 202), (status, created)
    request_id = created["request_id"]
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        status, body = _rest(server, "GET", f"/api/v1/requests/{request_id}")
        request = (body.get("requests") or [{}])[0]
        if request.get("status") == "complete" and request.get("machine_ids"):
            return {"request_id": request_id, "machine_id": request["machine_ids"][0]}
        time.sleep(2)
    raise AssertionError(f"the seeded request {request_id} did not complete: {body}")


def _probe(smoke: Smoke, server: Server, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the websocket helper in the installed package; retry only a refused connection."""
    command = [
        str(smoke.venv.python),
        "-I",
        str(smoke.helpers / "ws_probe.py"),
        server.base,
        json.dumps(steps),
    ]
    for attempt in (1, 2):
        done = subprocess.run(
            command, cwd=smoke.work, env=smoke.env(), capture_output=True, text=True,
            timeout=300, check=False,
        )  # fmt: skip
        print(f"websocket attempt {attempt}: exit {done.returncode}\n{done.stdout}\n{done.stderr}")
        if done.returncode == 3 and attempt == 1:
            continue
        assert done.returncode == 0, f"websocket probe failed:\n{done.stdout}\n{done.stderr}"
        return json.loads(done.stdout.strip().splitlines()[-1])
    raise AssertionError("unreachable")


@pytest.fixture(scope="module")
def page_loads(smoke: Smoke, server: Server, seeded: dict[str, str]) -> dict[str, Any]:
    steps = [
        {"state": state, "event": "load", "path": route}
        for route, state in {**DATA_PAGES, **STATIC_PAGES}.items()
    ]
    return _probe(smoke, server, steps)


def test_server_announces_its_urls_and_is_healthy(server: Server):
    assert URL_LINE.search(server.log_text()), server.log_text()[-2000:]
    announced = URL_LINE.search(server.log_text())
    assert announced is not None and f":{server.port}" in announced.group(1)
    with urllib.request.urlopen(f"{server.base}/", timeout=30) as response:  # noqa: S310
        assert response.status == 200


def test_page_inventory_is_pinned(smoke: Smoke):
    out = smoke.run_python(str(smoke.helpers / "introspect.py"), "pages")
    info = json.loads(out.stdout)
    routes = {"/" if r == "index" else f"/{r}" for r in info["routes"]}
    assert len(routes) >= MIN_PAGES and "/" in routes, sorted(routes)
    classified = set(DATA_PAGES) | set(STATIC_PAGES)
    assert routes == classified, (
        f"classify pages in DATA_PAGES or STATIC_PAGES: {sorted(routes ^ classified)}"
    )
    lock = (Path(__file__).resolve().parents[2] / "uv.lock").read_text()
    pinned = re.search(r'name = "reflex"\nversion = "([^"]+)"', lock)
    assert pinned is not None and info["reflex"] == pinned.group(1), (
        f"reflex {info['reflex']} is installed but uv.lock pins "
        f"{pinned.group(1) if pinned else '?'}; re-check how the page list is read"
    )


def test_every_page_loads_without_an_error(page_loads: dict[str, Any], server: Server):
    for route, state in {**DATA_PAGES, **STATIC_PAGES}.items():
        values = page_loads[state.rpartition(":")[2]]
        assert values, f"{route}: the load event answered with no state"
        assert not values.get("error"), f"{route}: error in state: {values.get('error')!r}"
    assert "StateSerializationError" not in server.log_text()


def _blob(values: dict[str, Any], name: str) -> str:
    return json.dumps(values.get(name, ""), default=str)


def test_pages_show_the_seeded_rows(page_loads: dict[str, Any], seeded: dict[str, str]):
    assert seeded["request_id"] in _blob(page_loads["RequestsState"], "requests")
    assert seeded["machine_id"] in _blob(page_loads["MachinesState"], "machines")
    assert "RunInstances-OnDemand" in _blob(page_loads["TemplatesState"], "templates")
    dashboard = page_loads["DashboardState"]
    handled = sum(int(dashboard.get(k) or 0) for k in ("completed_requests", "failed_requests"))
    assert handled >= 1, dashboard


def test_default_state_manager_writes_state_to_disk(
    smoke: Smoke, page_loads: dict[str, Any], server: Server
):
    """The disk state manager is the default; a memory-only run would leave no files."""
    ui = smoke.venv.site_packages() / "orb" / "ui"
    places = [ui / ".states", smoke.work / ".states"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(p.glob("*.pkl") for p in places if p.is_dir()):
            return
        time.sleep(0.1)
    listing = {str(p): sorted(x.name for x in p.iterdir()) if p.is_dir() else None for p in places}
    pytest.fail(f"no state file was written by the default state manager: {listing}")


def test_rejected_template_is_a_client_error_in_rest_and_in_the_ui(
    smoke: Smoke, server: Server, cli_flow: Flow
):
    spot = [
        t["template_id"]
        for t in cli_flow.templates
        if t["template_id"] == SPOT_TEMPLATE
        or (t.get("provider_api") == "RunInstances" and "spot" in t["template_id"].lower())
    ]
    assert spot, "templates list has no spot RunInstances template"
    template_id = spot[0]

    status, body = _rest(
        server, "POST", "/api/v1/machines/request", {"template_id": template_id, "count": 1}
    )
    ui = _probe(
        smoke,
        server,
        [
            {
                "state": "orb.ui.components.request_modal:RequestModalState",
                "event": "open_for",
                "payload": {"template_id": template_id},
                "idle": 1.0,
            },
            {
                "state": "orb.ui.components.request_modal:RequestModalState",
                "event": "submit",
                "idle": 3.0,
            },
        ],
    )["RequestModalState"]
    error = str(ui.get("error") or "")

    assert status == 400, (status, body)
    reason = ((body.get("error") or {}).get("details") or {}).get("reason", "")
    assert "does not support spot instances" in reason, body
    assert error.startswith("Request failed:"), error
    assert "400" in error and "500" not in error, error
    assert not ui.get("last_request_id"), ui
