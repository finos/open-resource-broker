"""Morgan Stanley makes this available to you under the Apache License,
Version 2.0 (the "License"). You may obtain a copy of the License at
http://www.apache.org/licenses/LICENSE-2.0. See the NOTICE file
distributed with this work for additional information regarding
copyright ownership. Unless required by applicable law or agreed
to in writing, software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.
See the License for the specific language governing permissions and
limitations under the License. Watch and manage open-resource-broker machine
requests and pods in a Kubernetes cluster.

Top level events watcher
"""

import json
import logging
import pathlib
import queue
import signal
import sqlite3
import sys
import time
from collections import defaultdict
from collections.abc import Generator
from datetime import datetime
from threading import Lock
from traceback import print_exc
from typing import Any

from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY
from prometheus_client.core import CounterMetricFamily
from prometheus_client.core import GaugeMetricFamily
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from open_resource_broker import fsutils
from open_resource_broker import k8sutils
from open_resource_broker.cli import context

logger = logging.getLogger(__name__)


class ConsoleEventBackend:
    """Dump events to console, intended for debugging"""

    def __init__(self: "ConsoleEventBackend", use_stderr=False, indent=None) -> None:
        """Init the console backend"""
        self.fd = sys.stderr if use_stderr else sys.stdout
        self.indent = indent

    def post(self: "ConsoleEventBackend", events) -> None:
        """Post event to the console"""
        for event in events:
            if event.get("sqlite_backup"):
                print(
                    f"Backup requested: {event['sqlite_backup']}",
                    file=self.fd,
                    flush=True,
                )
                continue
            if "state" in event.get("category", None):
                continue
            print(json.dumps(event, indent=self.indent), file=self.fd, flush=True)

    def close(self: "ConsoleEventBackend") -> None:
        """N/A"""


class KubeState:
    """State of objects (pods, nodes) in cluster."""

    common_prefix = ""
    common_labels = ()

    def __init__(self: "KubeState", ttl: int = 900) -> None:
        """init the state"""
        self.ttl = ttl
        self.last_expiry = time.time()
        self.objs: dict[str, Any] = {}

    def _expire(self: "KubeState") -> None:
        """Remove stale objects from state"""
        now = time.time()
        if now - self.last_expiry < self.ttl / 30:
            return
        stale_uids = tuple(
            uid
            for uid, (timestamp, obj) in self.objs.items()
            if obj.deletion_timestamp and now - timestamp > self.ttl
        )
        for uid in stale_uids:
            self.objs.pop(uid)
        self.last_expiry = now

    def _update(self: "KubeState", objs, force: bool = False) -> bool:
        """Update the existing objects or add them if not present"""
        now = time.time()
        if force:
            self.objs.clear()
            for obj in objs:
                self.objs[obj.uid] = now, obj
        else:
            for obj in objs:
                uid = obj.uid
                if uid not in self.objs:
                    self.objs[uid] = now, obj
                else:
                    _, existing_obj = self.objs[uid]
                    if existing_obj.version < obj.version:
                        self.objs[uid] = now, obj
            self._expire()
        return True

    def collect(self: "KubeState") -> Generator:
        """Export the current state"""
        if not self.objs:
            return
        self._expire()
        cpu_gauge = GaugeMetricFamily(
            self.common_prefix + "cpu", "", labels=self.common_labels
        )
        memory_gauge = GaugeMetricFamily(
            self.common_prefix + "memory", "", labels=self.common_labels
        )
        restarts_gauge = GaugeMetricFamily(
            self.common_prefix + "restarts", "", labels=self.common_labels
        )
        condition_gauge = GaugeMetricFamily(
            self.common_prefix + "condition",
            "",
            labels=(*self.common_labels, "type"),
        )
        for _, obj in self.objs.values():
            obj_labels = tuple(  # type: ignore
                (getattr(obj, label) or "") for label in self.common_labels
            )
            cpu_gauge.add_metric(obj_labels, obj.cpu)
            memory_gauge.add_metric(obj_labels, obj.memory)
            restart_count = getattr(obj, "restart_count", 0)
            if restart_count:
                restarts_gauge.add_metric(obj_labels, restart_count)
            for condition in obj.conditions:
                if condition["status"] == "True":
                    condition_type = condition.get("type")
                    if condition_type:
                        condition_gauge.add_metric(
                            (*obj_labels, str(condition_type)),
                            1,
                        )
        yield cpu_gauge
        yield memory_gauge
        yield restarts_gauge
        yield condition_gauge


class PodsState(KubeState):
    """State of pods in cluster."""

    common_prefix = "pod_"
    common_labels = (
        "pod_name",
        "pod_type",
        "node_name",
        "namespace",
        "pod_ip",
        "node_ip",
        "phase",
        "image",
        "start_time",
        "end_time",
    )

    def post(self: "PodsState", event) -> bool:
        """accept the events of interest and return True
        as the indication of "no further processing needed"
        """
        category = event.get("category", None)
        if category == "pods-state":
            return self._update(map(k8sutils.Pod, event["pods"]), force=True)
        if category == "pod" and isinstance(event.get("event"), dict):
            return self._update([k8sutils.Pod(event["event"]["object"])])
        return False


class NodesState(KubeState):
    """State of nodes in cluster."""

    common_prefix = "node_"
    common_labels = (
        "node_name",
        "node_ip",
        "zone_id",
        "instance_type",
        "capacity_type",
        "start_time",
        "end_time",
    )

    def post(self: "NodesState", event) -> bool:
        """accept the events of interest and return True
        as the indication of "no further processing needed"
        """
        category = event.get("category", None)
        if category == "nodes-state":
            return self._update(map(k8sutils.Node, event["nodes"]), force=True)
        if category == "node" and isinstance(event.get("event"), dict):
            return self._update([k8sutils.Node(event["event"]["object"])])
        return False


class PrometheusEventBackend:
    """Expose the metrics from the event stream and rebuilt pods view"""

    def __init__(
        self: "PrometheusEventBackend",
        addr="127.0.0.1",
        port=8080,
        ttl=3600,
    ) -> None:
        """Init the Prometheus backend"""
        self.ttl = ttl
        if port:
            logger.info("Starting prometheus exporter at: %s:%s", addr, port)
            self.server, self.thread = start_http_server(
                addr=addr,
                port=port,
            )
        else:
            self.server = self.thread = None
        self.lock = Lock()
        self.metrics: dict[tuple, tuple] = {}
        self.pods = PodsState()
        self.nodes = NodesState()
        self.machine_request_counters: dict[str, int] = defaultdict(int)
        self.machine_return_counter: int = 0
        REGISTRY.register(self)

    def _expire(self: "PrometheusEventBackend") -> None:
        expiry_timestamp = time.time() - self.ttl
        expired_metrics = tuple(
            k
            for k, (timestamp, value) in self.metrics.items()
            if timestamp < expiry_timestamp
        )
        for k in expired_metrics:
            self.metrics.pop(k)

    def collect(self: "PrometheusEventBackend") -> Generator:
        """Export the buffered metrics"""
        try:
            with self.lock:
                self._expire()

                for (name, label_names, label_values), (
                    timestamp,
                    value,
                ) in self.metrics.items():
                    gauge = GaugeMetricFamily(name, "", labels=label_names)
                    gauge.add_metric(label_values, value, timestamp)
                    yield gauge

                request_counters = CounterMetricFamily(
                    "machines_requested", "", labels=["template_id"]
                )
                for template_id, count in self.machine_request_counters.items():
                    request_counters.add_metric([template_id], count)
                yield request_counters

                return_counter = CounterMetricFamily("machines_returned", "", labels=[])
                return_counter.add_metric([], self.machine_return_counter)
                yield return_counter

                yield from self.pods.collect()
                yield from self.nodes.collect()
        except BaseException:
            with open("/tmp/hf-collector-trace.log", "at") as f:  # noqa: PTH123, S108, UP015, RUF100
                print(f"\n\n\n{datetime.utcnow()}\n\n\n", file=f)
                print_exc(file=f)
            raise

    def _parse_event(
        self: "PrometheusEventBackend",
        event,
    ) -> tuple[dict, tuple, tuple]:
        metrics = {}
        labels = {}
        for k, v in event.items():
            if k in ("category", "timestamp"):
                continue
            if isinstance(v, int | float):
                metrics[k] = v
            elif isinstance(v, str) and v:
                labels[k] = v
        label_names = []
        label_values = []
        for label_name, label_value in sorted(labels.items()):
            label_names.append(label_name)
            label_values.append(label_value)
        return metrics, tuple(label_names), tuple(label_values)

    def post(self: "PrometheusEventBackend", events) -> None:  # noqa: C901
        """Select metrics from event stream and store them for in the buffer
        so that they can be later scraped.
        """
        try:
            with self.lock:
                for event in events:
                    if self.pods.post(event):
                        continue
                    if self.nodes.post(event):
                        continue
                    category = event.get("category", None)
                    if category == "request" and event.get("template_id"):
                        self.machine_request_counters[event.get("template_id")] += (
                            event["count"]
                        )
                        continue
                    if category == "return" and event.get("count"):
                        self.machine_return_counter += event.get("count")
                        continue
                    """We expect metrics in the following format:
                    {
                        "category": "metric",
                        "timestamp": SECONDS,
                        "metric_1": NUMBER_1,
                        "metric_N": NUMBER_N,
                        "label_1": "text_1",
                        "label_N": "text_N",
                    }
                    """
                    if category != "metric":
                        continue
                    timestamp = event.get("timestamp")
                    if not isinstance(timestamp, int | float):
                        continue
                    metrics, label_names, label_values = self._parse_event(event)
                    for name, value in metrics.items():
                        key = (name, label_names, label_values)
                        existing = self.metrics.get(key)
                        if existing and existing[0] >= timestamp:
                            continue
                        self.metrics[key] = (timestamp, value)

                self._expire()
        except BaseException:
            with open("/tmp/hf-poster-trace.log", "at") as f:  # noqa: PTH123, S108, UP015, RUF100
                print(f"\n\n\n{datetime.utcnow()}\n\n\n", file=f)
                print_exc(file=f)
            raise

    def close(self: "PrometheusEventBackend") -> None:
        """Stop the prometheus exporter"""
        if self.server:
            self.server.shutdown()
        if self.thread:
            self.thread.join()


class SqliteEventBackend:
    """Dump events into a SQLite db"""

    def __init__(
        self: "SqliteEventBackend",
        dbfile=None,
        rotate=True,
        skip_events=False,
    ) -> None:
        """Initialize database."""
        self.dbfile = dbfile or context.GLOBAL.dbfile
        if self.dbfile is None:
            raise ValueError("Database file path is not provided.")
        self.rotate = rotate
        self.skip_events = skip_events

        self.known_columns = frozenset(
            (
                "hf_namespace",
                "hf_pod",
                "category",
                "id",
                "timestamp",
            )
        )

        self.conn = None
        if self.rotate:
            signal.signal(signal.SIGHUP, self.sighup)

    def sighup(self: "SqliteEventBackend", signum, frame) -> None:  # noqa: ARG002
        """Handle the SIGHUP"""
        self.close()

    def open(self: "SqliteEventBackend") -> None:
        """Initialize the actual db file"""
        logger.info("Initialize database: %s", self.dbfile)
        pathlib.Path(self.dbfile).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.dbfile)

        with self.conn as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    hf_namespace TEXT,
                    hf_pod TEXT,
                    category TEXT,
                    id TEXT,
                    timestamp INT,
                    type TEXT,
                    value TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS events_idx ON events(
                    hf_namespace,
                    hf_pod,
                    category,
                    id,
                    timestamp,
                    type,
                    value
                )
                """
            )

    def _prepare_event_for_db(
        self: "SqliteEventBackend", event: dict[str, Any]
    ) -> list[tuple[Any]] | None:
        """Prepare event for SQLite database."""
        # Initialize the formatted event with known columns
        known_columns_data = {col: event.get(col, "") for col in self.known_columns}
        known_columns = self.known_columns | frozenset(
            ("event", "events") if self.skip_events else ()
        )
        # Find the first unknown column (SQLite schema supports one type-value pair)
        unknown_columns_data = [
            (k, v) for k, v in event.items() if k not in known_columns
        ]

        if unknown_columns_data:
            # Format the event by merging known columns with the unknown column's
            # type and value
            formatted_event_values = []
            for key, value in unknown_columns_data:
                type_value_pair = {
                    "type": key,
                    "value": (
                        value
                        if isinstance(value, int | float | str)
                        else json.dumps(value, sort_keys=True)
                    ),
                }

                # Sort the merged dictionary and get the values alone in tuple
                formatted_event_data = tuple(
                    value
                    for _, value in sorted(
                        (known_columns_data | type_value_pair).items()
                    )
                )

                formatted_event_values.append(formatted_event_data)

            return formatted_event_values

        # No unknown columns, skip this event
        return None

    def _create_backup(self: "SqliteEventBackend", event: dict) -> None:
        """Create a backup of the given dbfile
        using the sqlite3 backup API.

        input event dict should contain:
        {
            "sqlite_backup": "identifier_string",
            "sqlite_backup_pages": 1000  # optional
        }
        """
        identifier = event.get("sqlite_backup")
        pages = event.get("sqlite_backup_pages", 1000)

        def _sanitize_identifier(id_str: str) -> str:
            return "".join(c for c in id_str if c.isalnum() or c in ("-", "_")).rstrip()

        identifier = (
            f"{_sanitize_identifier(identifier)}_"
            f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        )

        backup_dir = pathlib.Path(self.dbfile).parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"events_bkp_{identifier}.db"

        def _progress_callback(status, remaining, total) -> None:  # noqa: ARG001
            """Callback function to report backup progress."""
            percent = (total - remaining) / total * 100
            # Write backup progress to a file under backup_dir using pathlib
            progress_file = backup_dir / f"backup_progress_{identifier}.log"
            progress_file.parent.mkdir(parents=True, exist_ok=True)
            progress_data = {
                "copied_pages": total - remaining,
                "total_pages": total,
                "percent_complete": percent,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            with progress_file.open("a") as pf:
                pf.write(json.dumps(progress_data) + "\n")

        try:
            # Ensure all transactions are committed and WAL is checkpointed
            if self.conn is None:
                self.conn = sqlite3.connect(self.dbfile)
                self.conn.execute("PRAGMA wal_checkpoint(FULL);")
                self.conn.commit()
            with sqlite3.connect(backup_path) as dest_conn:
                self.conn.backup(dest_conn, pages=pages, progress=_progress_callback)
                logger.info(
                    "Backup created at %s (source: %s)", backup_path, self.dbfile
                )
        except sqlite3.DatabaseError as exc:
            logger.error("Failed to create backup at %s: %s", backup_path, exc)

    def post(self: "SqliteEventBackend", events: list[dict[str, Any]]) -> None:
        """Post given events to the underlying SQLite database."""
        if not events:
            return

        db_events = []
        for event in events:
            if event.get("sqlite_backup"):
                self._create_backup(event)
                continue
            category = event.get("category", None)
            if category and ("state" in category or category == "metric"):
                continue
            formatted_event = self._prepare_event_for_db(event)
            if formatted_event:
                db_events.extend(formatted_event)

        if not db_events:
            return

        # Prepare SQL query components
        sorted_columns = sorted(self.known_columns | {"type", "value"})
        columns_str = ", ".join(sorted_columns)
        placeholders = ", ".join("?" for _ in sorted_columns)

        if self.conn is None:
            self.open()

        # Execute the query in a single transaction
        # All statements that run within the same "with ..." context
        # are executed in the context or single transaction.
        # Clumping them into one statement is not needed.
        with self.conn as conn:
            conn.executemany(
                f"""
                INSERT INTO events ({columns_str}) VALUES ({placeholders})
                ON CONFLICT DO NOTHING
                """,  # noqa: S608
                db_events,
            )
        logger.info("Inserted events into the database: %s", db_events)

    def close(self: "SqliteEventBackend") -> None:
        """Close the db"""
        if self.conn is None:
            return
        self.conn.close()
        self.conn = None
        if self.rotate:
            dbpath = pathlib.Path(self.dbfile)
            if dbpath.exists():
                dbpath.rename(dbpath.with_suffix(f".{int(time.time() * 1000)}"))


def _pending_events(eventdir) -> tuple[pathlib.Path]:
    return tuple(fsutils.iterate_directory(directory=eventdir, files_only=True))


def _process_events(eventfiles, backends) -> None:
    """Process events from the given list of event files."""
    all_events = []
    try:
        for eventfile in eventfiles:
            logger.info("Processing event in: %s", eventfile)
            try:
                events = json.loads(eventfile.read_text())
                if not isinstance(events, list | tuple):
                    events = [events]
                all_events.extend(events)
            except ValueError:
                logger.warning("Invalid JSON in file: %s", eventfile)
            finally:
                eventfile.unlink(missing_ok=True)
    finally:
        if all_events:
            backend_exception = None
            for backend in backends:
                try:
                    backend.post(all_events)
                except BaseException as e:  # noqa: BLE001
                    backend_exception = e
            if backend_exception:
                raise backend_exception


class _EventFileHandler(FileSystemEventHandler):
    """Watchdog event handler for file system events."""

    def __init__(self, event_queue: queue.Queue) -> None:
        super().__init__()
        self.event_queue = event_queue

    def on_created(self, event) -> None:
        if not event.is_directory:
            self.event_queue.put(event.src_path)

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self.event_queue.put(event.dest_path)


def _watch_events(eventdir, backends) -> None:
    logger.info("Watching events in: %s", eventdir)

    try:
        _process_events(_pending_events(eventdir), backends)

        event_queue: queue.Queue = queue.Queue()
        handler = _EventFileHandler(event_queue)
        observer = Observer()
        observer.schedule(handler, str(eventdir), recursive=False)
        observer.start()

        try:
            while True:
                try:
                    event_queue.get(timeout=1.0)
                    _process_events(_pending_events(eventdir), backends)
                except queue.Empty:
                    pass
        finally:
            observer.stop()
            observer.join()
    finally:
        backend_exception = None
        for backend in backends:
            try:
                backend.close()
            except BaseException as e:  # noqa: BLE001
                backend_exception = e
        if backend_exception:
            raise backend_exception


def watch(
    eventdir: pathlib.Path,
    prometheus_addr: str | None = None,
    prometheus_port: int | None = None,
    prometheus_ttl: int = 3600,
    rotate_events: bool = True,
    skip_events: bool = True,
) -> None:
    """Prepare backends and watch for events"""
    backends = [
        ConsoleEventBackend(),
        SqliteEventBackend(
            rotate=rotate_events,
            skip_events=skip_events,
        ),
    ]

    if prometheus_addr and prometheus_port:
        backends.append(
            PrometheusEventBackend(
                addr=prometheus_addr,
                port=prometheus_port,
                ttl=prometheus_ttl,
            )
        )

    _watch_events(eventdir, backends)
