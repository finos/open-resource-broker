#!/usr/bin/env python
"""Mock slurmrestd server for local development and testing.

Simulates the SLURM REST API (v0.0.44) without a real cluster.
Supports node state transitions via /mock/resume and /mock/suspend endpoints.

Usage:
    python slurmmock.py --port 6820 --nodes 10 --partitions batch,gpu
"""

import argparse
import json
import logging
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger("slurmmock")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [slurmmock] %(levelname)s %(message)s")

API_VERSION = "v0.0.44"


class ClusterState:
    """In-memory simulated SLURM cluster state."""

    def __init__(self, num_nodes: int, partitions: list[str], delay: float):
        self._lock = threading.Lock()
        self._delay = delay
        self._partitions = partitions
        self._nodes: dict[str, dict] = {}
        # Distribute nodes across partitions
        for i in range(1, num_nodes + 1):
            name = f"compute-{i:03d}"
            partition = partitions[(i - 1) % len(partitions)]
            self._nodes[name] = {
                "name": name,
                "state": ["POWERED_DOWN"],
                "cpus": 4,
                "real_memory": 8192,
                "partitions": [partition],
            }

    def get_nodes(self) -> list[dict]:
        with self._lock:
            return list(self._nodes.values())

    def get_node(self, name: str) -> dict | None:
        with self._lock:
            return self._nodes.get(name)

    def get_partitions(self) -> list[dict]:
        result = []
        with self._lock:
            for pname in self._partitions:
                nodes_in_part = [n for n in self._nodes.values() if pname in n["partitions"]]
                result.append(
                    {
                        "name": pname,
                        "state": {"current": ["UP"]},
                        "nodes": {"total": len(nodes_in_part)},
                        "cpus": {"total": sum(n["cpus"] for n in nodes_in_part)},
                    }
                )
        return result

    def get_partition(self, name: str) -> dict | None:
        for p in self.get_partitions():
            if p["name"] == name:
                return p
        return None

    def resume_nodes(self, node_names: list[str]) -> None:
        """Transition nodes: POWERED_DOWN → POWERING_UP → IDLE."""
        with self._lock:
            for name in node_names:
                if name in self._nodes:
                    self._nodes[name]["state"] = ["POWERING_UP"]

        def _finish():
            time.sleep(self._delay)
            with self._lock:
                for name in node_names:
                    if name in self._nodes:
                        self._nodes[name]["state"] = ["IDLE"]
            log.info("Nodes transitioned to IDLE: %s", node_names)

        threading.Thread(target=_finish, daemon=True).start()

    def suspend_nodes(self, node_names: list[str]) -> None:
        """Transition nodes: * → POWERING_DOWN → POWERED_DOWN."""
        with self._lock:
            for name in node_names:
                if name in self._nodes:
                    self._nodes[name]["state"] = ["POWERING_DOWN"]

        def _finish():
            time.sleep(self._delay)
            with self._lock:
                for name in node_names:
                    if name in self._nodes:
                        self._nodes[name]["state"] = ["POWERED_DOWN"]
            log.info("Nodes transitioned to POWERED_DOWN: %s", node_names)

        threading.Thread(target=_finish, daemon=True).start()


class SlurmMockHandler(BaseHTTPRequestHandler):
    """HTTP handler mimicking slurmrestd v0.0.44."""

    cluster: ClusterState  # set by server

    def log_message(self, format, *args):
        log.info("%s %s", self.command, self.path)

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        prefix = f"/slurm/{API_VERSION}"

        if self.path == f"{prefix}/diag":
            self._send_json(
                {
                    "meta": {"Slurm": {"release": "25.11.0"}},
                    "errors": [],
                }
            )
        elif self.path == f"{prefix}/nodes":
            self._send_json({"nodes": self.cluster.get_nodes(), "errors": []})
        elif self.path.startswith(f"{prefix}/node/"):
            name = self.path[len(f"{prefix}/node/") :]
            node = self.cluster.get_node(name)
            if node:
                self._send_json({"nodes": [node], "errors": []})
            else:
                self._send_json({"nodes": [], "errors": [{"error": "Node not found"}]}, 404)
        elif self.path == f"{prefix}/partitions":
            self._send_json({"partitions": self.cluster.get_partitions(), "errors": []})
        elif self.path.startswith(f"{prefix}/partition/"):
            name = self.path[len(f"{prefix}/partition/") :]
            partition = self.cluster.get_partition(name)
            if partition:
                self._send_json({"partitions": [partition], "errors": []})
            else:
                self._send_json(
                    {"partitions": [], "errors": [{"error": "Partition not found"}]}, 404
                )
        else:
            self._send_json({"errors": [{"error": "Not found"}]}, 404)

    def do_POST(self):
        if self.path == "/mock/resume":
            data = self._read_body()
            nodes = data.get("nodes", [])
            self.cluster.resume_nodes(nodes)
            self._send_json({"message": f"Resuming {len(nodes)} nodes", "nodes": nodes})
        elif self.path == "/mock/suspend":
            data = self._read_body()
            nodes = data.get("nodes", [])
            self.cluster.suspend_nodes(nodes)
            self._send_json({"message": f"Suspending {len(nodes)} nodes", "nodes": nodes})
        else:
            self._send_json({"errors": [{"error": "Not found"}]}, 404)


def main():
    parser = argparse.ArgumentParser(description="Mock slurmrestd server")
    parser.add_argument("--port", type=int, default=6820, help="Listen port (default: 6820)")
    parser.add_argument(
        "--nodes", type=int, default=10, help="Number of simulated nodes (default: 10)"
    )
    parser.add_argument(
        "--partitions", default="batch", help="Comma-separated partition names (default: batch)"
    )
    parser.add_argument(
        "--delay", type=float, default=2.0, help="State transition delay in seconds (default: 2)"
    )
    args = parser.parse_args()

    partitions = [p.strip() for p in args.partitions.split(",")]
    cluster = ClusterState(num_nodes=args.nodes, partitions=partitions, delay=args.delay)

    SlurmMockHandler.cluster = cluster
    server = HTTPServer(("0.0.0.0", args.port), SlurmMockHandler)  # noqa: S104

    def shutdown(signum, frame):
        log.info("Shutting down...")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info(
        "Mock slurmrestd started on port %d (%d nodes, partitions: %s, delay: %.1fs)",
        args.port,
        args.nodes,
        partitions,
        args.delay,
    )
    log.info("Endpoints: GET /slurm/%s/{diag,nodes,partitions}", API_VERSION)
    log.info("Mock controls: POST /mock/{resume,suspend}")
    server.serve_forever()


if __name__ == "__main__":
    main()
