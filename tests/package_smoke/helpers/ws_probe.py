"""Drive the UI's websocket state events from the installed package.

Runs with the smoke venv's interpreter (``python -I ws_probe.py <base-url>
<steps-json>``), so ``orb`` and the socket.io client come from the installed
wheel, not from the checkout. Each step names a state class and one of its
event handlers; the script sends them over one connection, merges the deltas
the server answers with and prints the resulting state variables as JSON,
keyed by state class name.

Exit status 3 means the websocket connection could not be made; the caller may
retry once. Any other failure is a failure of the check itself.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import time
import uuid

import socketio

NAMESPACE = "/_event"
SUFFIX = "_rx_state_"
CONNECT_ERROR = 3


def _state_class(spec: str):
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


async def _settle(last_delta: list[float], idle: float, limit: float) -> None:
    """Wait until no delta arrived for ``idle`` seconds, or ``limit`` passed."""
    started = time.monotonic()
    while time.monotonic() - started < limit:
        await asyncio.sleep(0.25)
        if time.monotonic() - last_delta[0] >= idle:
            return


async def main(base_url: str, steps: list[dict]) -> int:
    token = str(uuid.uuid4())
    client = socketio.AsyncClient()
    merged: dict[str, dict] = {}
    last_delta = [time.monotonic()]

    @client.on("event", namespace=NAMESPACE)
    async def _on_event(update):
        last_delta[0] = time.monotonic()
        for state_name, values in (update.get("delta") or {}).items():
            merged.setdefault(state_name, {}).update(values)

    try:
        await client.connect(
            f"{base_url}?token={token}",
            socketio_path=NAMESPACE,
            namespaces=[NAMESPACE],
            transports=["websocket"],
            wait_timeout=15,
        )
    except Exception as exc:
        print(json.dumps({"connect_error": f"{type(exc).__name__}: {exc}"}))
        return CONNECT_ERROR

    try:
        for step in steps:
            state = _state_class(step["state"])
            last_delta[0] = time.monotonic()
            await client.emit(
                "event",
                {
                    "token": token,
                    "name": f"{state.get_full_name()}.{step['event']}",
                    "router_data": {"pathname": step.get("path", "/")},
                    "payload": step.get("payload", {}),
                },
                namespace=NAMESPACE,
            )
            await _settle(last_delta, step.get("idle", 2.5), step.get("limit", 30.0))
    finally:
        await client.disconnect()

    result: dict[str, dict] = {}
    for step in steps:
        state = _state_class(step["state"])
        values = merged.get(state.get_full_name(), {})
        result[step["state"].rpartition(":")[2]] = {
            key.removesuffix(SUFFIX): value
            for key, value in values.items()
            if not key.startswith("rx_router")
        }
    print(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], json.loads(sys.argv[2]))))
