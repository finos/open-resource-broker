"""Minimal SSE client over httpx — async generator yielding (event, data) tuples.

Reflex apps run inside an asyncio event loop, so we use httpx's stream API
to keep the connection open and parse each event as it arrives. This file
intentionally has no external dependencies beyond httpx.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


async def stream_sse(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Yield ``(event_type, data)`` tuples from a server-sent-events stream.

    Skips ``heartbeat`` events. Parses ``data:`` payload as JSON. If
    payload is not valid JSON, yields the raw string under data={"raw": "..."}.
    Auto-reconnects on transport error with a 1s backoff (max 30s).
    """
    backoff = 1.0
    timeout = timeout or httpx.Timeout(None)  # SSE streams stay open
    while True:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("GET", url, headers=headers or {}) as resp:
                    if resp.status_code != 200:
                        # endpoint missing / unauthorised — back off
                        raise httpx.HTTPStatusError(
                            f"SSE returned {resp.status_code}", request=resp.request, response=resp
                        )
                    backoff = 1.0  # reset on successful connect
                    event_type = "message"
                    data_buf: list[str] = []
                    async for line in resp.aiter_lines():
                        if line == "":
                            # dispatch event
                            if data_buf:
                                raw = "\n".join(data_buf)
                                data_buf = []
                                if event_type == "heartbeat":
                                    event_type = "message"
                                    continue
                                try:
                                    data = json.loads(raw)
                                except Exception:
                                    data = {"raw": raw}
                                yield event_type, data
                            event_type = "message"
                            continue
                        if line.startswith(":"):
                            continue  # comment / keep-alive
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_buf.append(line[5:].lstrip())
        except (httpx.HTTPError, httpx.TransportError):
            import asyncio

            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)
            continue
