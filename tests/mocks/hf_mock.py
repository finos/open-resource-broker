"""Clean in-process mock for HostFactory scheduler operations.

Replaces the legacy hfmock.py which invoked shell scripts via subprocess.
This mock operates entirely in-process using standard Python data structures,
making it suitable for fast, isolated unit and integration tests.

Supports both scheduler wire formats:
- "hostfactory": camelCase keys (templateId, machineCount, requestId, machineId)
- "default": snake_case keys (template_id, machine_count, request_id, machine_id)
"""

from __future__ import annotations

import uuid
from typing import Any


class HostFactoryMock:
    """In-process mock of HostFactory scheduler operations.

    Maintains internal state for templates, requests, and machines so tests
    can exercise the full request/status/return lifecycle without hitting
    any external process or AWS endpoint.

    Usage::

        mock = HostFactoryMock(scheduler="hostfactory")
        mock.add_template("tmpl-1", {"templateId": "tmpl-1", "providerApi": "ASG"})

        res = mock.request_machines("tmpl-1", 2)
        request_id = res["requestId"]

        mock.complete_request(request_id)

        status = mock.get_request_status(request_id)
        assert status["requests"][0]["status"] == "complete"
    """

    def __init__(self, scheduler: str = "hostfactory") -> None:
        """Initialise the mock.

        Args:
            scheduler: Wire format to use — "hostfactory" (camelCase) or
                       "default" (snake_case).
        """
        if scheduler not in ("hostfactory", "default"):
            raise ValueError(
                f"Unknown scheduler type: {scheduler!r}. Use 'hostfactory' or 'default'."
            )
        self.scheduler = scheduler

        # Internal state
        self._templates: dict[str, dict[str, Any]] = {}
        self._requests: dict[str, dict[str, Any]] = {}
        self._return_requests: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Test setup helpers
    # ------------------------------------------------------------------

    def add_template(self, template_id: str, template_data: dict[str, Any]) -> None:
        """Register a template so it appears in get_available_templates responses."""
        self._templates[template_id] = dict(template_data)

    def complete_request(self, request_id: str, machine_ids: list[str] | None = None) -> None:
        """Transition a pending request to 'complete' with optional machine list.

        If machine_ids is not provided, synthetic IDs are generated based on
        the requested count stored when request_machines was called.
        """
        req = self._requests.get(request_id)
        if req is None:
            raise KeyError(f"Unknown request_id: {request_id!r}")

        count = req.get("_requested_count", 1)
        ids = machine_ids or [f"i-{uuid.uuid4().hex[:17]}" for _ in range(count)]

        if self.scheduler == "hostfactory":
            machines = [{"machineId": mid, "status": "running", "result": "succeed"} for mid in ids]
        else:
            machines = [
                {"machine_id": mid, "status": "running", "result": "succeed"} for mid in ids
            ]

        req["status"] = "complete"
        req["machines"] = machines

    def fail_request(self, request_id: str, message: str = "Provisioning failed") -> None:
        """Transition a pending request to 'failed'."""
        req = self._requests.get(request_id)
        if req is None:
            raise KeyError(f"Unknown request_id: {request_id!r}")
        req["status"] = "failed"
        req["machines"] = []
        req["message"] = message

    # ------------------------------------------------------------------
    # HostFactory API surface (mirrors the legacy HostFactoryMock)
    # ------------------------------------------------------------------

    def get_available_templates(self) -> dict[str, Any]:
        """Return all registered templates in scheduler wire format."""
        return {"templates": list(self._templates.values())}

    def request_machines(self, template_id: str, machine_count: int) -> dict[str, Any]:
        """Create a provisioning request and return the request ID.

        The request starts in 'executing' status. Call complete_request() or
        fail_request() to advance it to a terminal state.
        """
        request_id = f"req-{uuid.uuid4().hex[:12]}"

        machines: list[dict[str, Any]] = []
        if self.scheduler == "hostfactory":
            machines = [
                {"machineId": f"i-pending-{i}", "status": "executing", "result": "executing"}
                for i in range(machine_count)
            ]
        else:
            machines = [
                {"machine_id": f"i-pending-{i}", "status": "executing", "result": "executing"}
                for i in range(machine_count)
            ]

        self._requests[request_id] = {
            "status": "executing",
            "machines": machines,
            "_requested_count": machine_count,
            "_template_id": template_id,
        }

        if self.scheduler == "hostfactory":
            return {"requestId": request_id, "message": "Request VM success."}
        else:
            return {"request_id": request_id, "message": "Request VM success."}

    def get_request_status(self, request_id: str) -> dict[str, Any]:
        """Return the current status of a provisioning request."""
        req = self._requests.get(request_id)
        if req is None:
            return {"requests": []}

        if self.scheduler == "hostfactory":
            entry: dict[str, Any] = {
                "requestId": request_id,
                "status": req["status"],
                "machines": req.get("machines", []),
            }
        else:
            entry = {
                "request_id": request_id,
                "status": req["status"],
                "machines": req.get("machines", []),
            }

        if "message" in req:
            entry["message"] = req["message"]

        return {"requests": [entry]}

    def request_return_machines(self, machine_ids: list[str]) -> dict[str, Any]:
        """Submit a return (termination) request for the given machine IDs."""
        return_request_id = f"ret-{uuid.uuid4().hex[:12]}"

        if self.scheduler == "hostfactory":
            machines = [{"machineId": mid, "status": "deleting"} for mid in machine_ids]
        else:
            machines = [{"machine_id": mid, "status": "deleting"} for mid in machine_ids]

        self._return_requests[return_request_id] = {
            "status": "executing",
            "machines": machines,
        }

        if self.scheduler == "hostfactory":
            return {"requestId": return_request_id, "message": "Delete VM success."}
        else:
            return {"request_id": return_request_id, "message": "Delete VM success."}

    def get_return_requests(self, machine_ids: list[str]) -> dict[str, Any]:
        """Return the status of return requests for the given machine IDs (or request IDs)."""
        # Accept either machine IDs or return-request IDs — look up by both
        results = []
        for mid in machine_ids:
            if mid in self._return_requests:
                req = self._return_requests[mid]
                if self.scheduler == "hostfactory":
                    results.append(
                        {"requestId": mid, "status": req["status"], "machines": req["machines"]}
                    )
                else:
                    results.append(
                        {"request_id": mid, "status": req["status"], "machines": req["machines"]}
                    )
            # Unknown ID — return empty entry
            elif self.scheduler == "hostfactory":
                results.append({"requestId": mid, "status": "unknown", "machines": []})
            else:
                results.append({"request_id": mid, "status": "unknown", "machines": []})

        return {"requests": results}
