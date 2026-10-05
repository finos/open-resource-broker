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

Test processing of events.
"""

import json
import queue as queue_module
import sqlite3
import tempfile
import time
from contextlib import closing
from unittest.mock import MagicMock

import pytest
from prometheus_client.core import REGISTRY

from orb.k8s_legacy import events, fsutils
from orb.k8s_legacy.cli import context
from orb.k8s_legacy.impl.watchers import events as events_impl
from orb.k8s_legacy.impl.watchers.events import (
    ConsoleEventBackend,
    NodesState,
    PodsState,
    PrometheusEventBackend,
    SqliteEventBackend,
    _EventFileHandler,
    _pending_events,
    _process_events,
    _watch_events,
    watch,
)


def test_post_events() -> None:
    """Test pod events in directory"""
    with tempfile.TemporaryDirectory() as dirname:
        context.GLOBAL.dirname = dirname

        with events.EventsBuffer() as buf:
            buf.post(
                {
                    "category": "pod",
                    "id": "abcd-0",
                    "request": "abcd",
                    "list": [1, 2, 3],
                    "obj": {"foo": "bar", "hello": "world"},
                }
            )

        found = False
        for eventfile in fsutils.iterate_directory(directory=dirname):
            payload = json.loads(eventfile.read_text())
            assert isinstance(payload, list | tuple)
            assert len(payload) == 1
            event = payload[0]
            assert event["category"] == "pod"
            assert event["id"] == "abcd-0"
            assert event["request"] == "abcd"
            assert event["list"] == [1, 2, 3]
            assert event["obj"] == {"foo": "bar", "hello": "world"}
            found = True
        assert found


def test_sqlite_events_backend() -> None:
    """Test pod events with sqlite."""
    backend = SqliteEventBackend(":memory:")
    backend.post(
        [
            {
                "category": "pod",
                "id": "abcd-0",
                "request": "abcd",
            }
        ]
    )

    with closing(backend.conn.cursor()) as cur:
        cur.execute("SELECT category, id, type, value FROM events")
        result = cur.fetchone()
        assert result == (
            "pod",
            "abcd-0",
            "request",
            "abcd",
        )

    backend.post(
        [
            {
                "category": "node",
                "id": "abcd-1",
                "pending": 10001,
            }
        ]
    )

    with closing(backend.conn.cursor()) as cur:
        cur.execute("SELECT category, id, type, value FROM events WHERE type='pending'")
        result = cur.fetchone()
        assert result == (
            "node",
            "abcd-1",
            "pending",
            "10001",
        )

    backend.post(
        [
            {
                "category": "event",
                "id": "abcd-2",
                "event": {"foo": "bar", "hello": "world"},
            }
        ]
    )

    with closing(backend.conn.cursor()) as cur:
        cur.execute("SELECT category, id, type, value FROM events WHERE type='event'")
        result = cur.fetchone()
        assert result == (
            "event",
            "abcd-2",
            "event",
            """{"foo": "bar", "hello": "world"}""",
        )


def test_sqlite_events_backend_skip_events() -> None:
    """Test if sqlite backend ignores events if requested."""
    backend = SqliteEventBackend(":memory:", skip_events=True)
    backend.post(
        [
            {
                "category": "pod",
                "id": "abcd-0",
                "request": "abcd",
            }
        ]
    )

    with closing(backend.conn.cursor()) as cur:
        cur.execute("SELECT category, id, type, value FROM events")
        result = cur.fetchone()
        assert result == (
            "pod",
            "abcd-0",
            "request",
            "abcd",
        )

    backend.post(
        [
            {
                "category": "event",
                "id": "abcd-2",
                "event": {"foo": "bar", "hello": "world"},
            }
        ]
    )

    with closing(backend.conn.cursor()) as cur:
        cur.execute("SELECT category, id, type, value FROM events WHERE type='event'")
        result = cur.fetchone()
        assert result is None


@pytest.fixture
def prometheus_backend():
    """A PrometheusEventBackend that unregisters from the global prometheus
    REGISTRY on teardown.

    PrometheusEventBackend.__init__ registers itself under fixed metric
    names ("machines_requested", "machines_returned"); the global REGISTRY
    rejects a second collector exposing the same names, so every test that
    constructs one of these backends must clean up after itself to avoid
    poisoning the rest of the suite / randomized test order.
    """
    backend = PrometheusEventBackend(port=None, ttl=1000)
    try:
        yield backend
    finally:
        backend.close()
        REGISTRY.unregister(backend)


def test_prometheus_events_backend(prometheus_backend) -> None:
    """Test events with prometheus."""
    now = time.time()
    backend = prometheus_backend

    backend.post(
        [
            {
                "category": "pod",
                "timestamp": now,
                "val": 123,
                "label1": "text1",
                "hello": "world",
            }
        ]
    )

    for metric in backend.collect():
        if metric.name not in (
            "machines_requested",
            "machines_returned",
        ):
            pytest.fail("Backend should hold no custom metrics")

    backend.post(
        [
            {
                "category": "metric",
                "timestamp": now - 500,
                "val": 123,
                "label1": "text1",
                "hello": "world",
            },
            {
                "category": "metric",
                "timestamp": now - 1500,
                "var": 123,
                "label2": "text2",
                "hello": "world",
            },
        ]
    )

    for metric in backend.collect():
        if metric.name not in (
            "machines_requested",
            "machines_returned",
        ):
            assert len(metric.samples) == 1
            sample = metric.samples[0]
            assert sample.name == "val"
            assert sample.value == 123
            assert sample.timestamp == now - 500
            assert sample.labels == {
                "label1": "text1",
                "hello": "world",
            }

    backend.ttl = 100

    for _ in backend.collect():
        if metric.name not in (
            "machines_requested",
            "machines_returned",
        ):
            pytest.fail("Backend should hold no more custom metrics")


# ---------------------------------------------------------------------------
# ConsoleEventBackend
# ---------------------------------------------------------------------------


def test_console_backend_posts_event_as_json(capsys) -> None:
    """Non-state events are dumped as JSON to the configured stream."""
    backend = ConsoleEventBackend()
    backend.post([{"category": "pod", "id": "abcd-0"}])
    captured = capsys.readouterr()
    assert json.loads(captured.out.strip()) == {"category": "pod", "id": "abcd-0"}
    backend.close()


def test_console_backend_skips_state_category(capsys) -> None:
    """Events whose category contains 'state' are not printed."""
    backend = ConsoleEventBackend()
    backend.post([{"category": "pods-state", "pods": []}])
    captured = capsys.readouterr()
    assert captured.out == ""


def test_console_backend_prints_backup_message_to_stderr(capsys) -> None:
    """sqlite_backup events print a backup notice instead of raw JSON."""
    backend = ConsoleEventBackend(use_stderr=True)
    backend.post([{"sqlite_backup": "snap1"}])
    captured = capsys.readouterr()
    assert "Backup requested: snap1" in captured.err
    assert captured.out == ""


# ---------------------------------------------------------------------------
# KubeState / PodsState / NodesState
# ---------------------------------------------------------------------------


def _pod_dict(uid="pod-uid-1", name="pod-1", version="10", restart_count=0) -> dict:
    return {
        "metadata": {
            "uid": uid,
            "name": name,
            "namespace": "default",
            "labels": {"app.kubernetes.io/name": "worker"},
            "creation_timestamp": 1700000000,
            "deletion_timestamp": None,
            "resource_version": version,
        },
        "spec": {
            "node_name": "node-1",
            "containers": [
                {
                    "image": "worker:1",
                    "resources": {"requests": {"cpu": "500m", "memory": "128Mi"}},
                }
            ],
        },
        "status": {
            "host_ip": "10.0.0.1",
            "pod_ip": "10.0.0.2",
            "phase": "Running",
            "start_time": 1700000000,
            "container_statuses": [{"restart_count": restart_count}],
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }


def _node_dict(uid="node-uid-1", name="node-1", version="5") -> dict:
    return {
        "metadata": {
            "uid": uid,
            "name": name,
            "labels": {"node.kubernetes.io/instance-type": "m5.large"},
            "creation_timestamp": 1700000000,
            "deletion_timestamp": None,
            "resource_version": version,
        },
        "status": {
            "addresses": [{"type": "InternalIP", "address": "10.0.1.1"}],
            "capacity": {"cpu": "2", "memory": "4Gi"},
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }


def test_pods_state_post_bulk_replaces_state() -> None:
    """A pods-state event force-replaces the known pod set."""
    state = PodsState()
    handled = state.post({"category": "pods-state", "pods": [_pod_dict()]})
    assert handled is True
    assert len(state.objs) == 1


def test_pods_state_post_single_pod_event_merges_by_version() -> None:
    """A single pod event is merged into state, newer version wins."""
    state = PodsState()
    state.post({"category": "pods-state", "pods": [_pod_dict(version="1")]})

    # Older/equal version does not override.
    state.post(
        {
            "category": "pod",
            "event": {"object": _pod_dict(version="1")},
        }
    )
    _, obj = state.objs["pod-uid-1"]
    assert obj.version == 1

    # Newer version replaces the stored object.
    state.post(
        {
            "category": "pod",
            "event": {"object": _pod_dict(version="2")},
        }
    )
    _, obj = state.objs["pod-uid-1"]
    assert obj.version == 2


def test_pods_state_post_ignores_unrelated_category() -> None:
    """Events outside the pod/pods-state categories are not consumed."""
    state = PodsState()
    handled = state.post({"category": "metric", "val": 1})
    assert handled is False
    assert state.objs == {}


def test_pods_state_post_ignores_non_dict_event_payload() -> None:
    """A 'pod' category event whose payload is not a dict is not consumed."""
    state = PodsState()
    handled = state.post({"category": "pod", "event": "not-a-dict"})
    assert handled is False


def test_nodes_state_post_bulk_replaces_state() -> None:
    """A nodes-state event force-replaces the known node set."""
    state = NodesState()
    handled = state.post({"category": "nodes-state", "nodes": [_node_dict()]})
    assert handled is True
    assert len(state.objs) == 1


def test_nodes_state_post_single_node_event() -> None:
    """A single node event is merged into state."""
    state = NodesState()
    handled = state.post({"category": "node", "event": {"object": _node_dict()}})
    assert handled is True
    assert "node-uid-1" in state.objs


def test_nodes_state_post_ignores_unrelated_category() -> None:
    """Events outside the node/nodes-state categories are not consumed."""
    state = NodesState()
    handled = state.post({"category": "request", "template_id": "t1", "count": 1})
    assert handled is False


def test_kube_state_collect_emits_metrics_for_known_objects() -> None:
    """collect() yields cpu/memory/restarts/condition gauges for stored objects."""
    state = PodsState()
    state.post({"category": "pods-state", "pods": [_pod_dict(restart_count=3)]})

    families = {family.name: family for family in state.collect()}
    assert set(families) == {
        "pod_cpu",
        "pod_memory",
        "pod_restarts",
        "pod_condition",
    }
    cpu_sample = families["pod_cpu"].samples[0]
    assert cpu_sample.value == 0.5
    restart_sample = families["pod_restarts"].samples[0]
    assert restart_sample.value == 3
    condition_sample = families["pod_condition"].samples[0]
    assert condition_sample.labels["type"] == "Ready"


def test_kube_state_collect_is_empty_when_no_objects() -> None:
    """collect() yields nothing when no objects have been tracked yet."""
    state = PodsState()
    assert list(state.collect()) == []


def test_kube_state_collect_skips_zero_restarts_and_non_ready_conditions() -> None:
    """A gauge sample is only emitted for truthy restart counts and
    conditions that are both status=True and carry a type."""
    state = PodsState()
    pod = _pod_dict(restart_count=0)
    pod["status"]["conditions"] = [
        {"type": "Ready", "status": "False"},
        {"status": "True"},  # True status but no "type" key
    ]
    state.post({"category": "pods-state", "pods": [pod]})

    families = {family.name: family for family in state.collect()}
    assert families["pod_restarts"].samples == []
    assert families["pod_condition"].samples == []


def test_kube_state_expire_removes_stale_deleted_objects() -> None:
    """Objects with a deletion_timestamp older than the TTL are expired."""
    state = PodsState(ttl=10)
    state.post(
        {
            "category": "pods-state",
            "pods": [_pod_dict()],
        }
    )
    uid, (_timestamp, obj) = next(iter(state.objs.items()))
    obj.deletion_timestamp = 1  # make the object eligible for expiry
    # Backdate the object's stored insertion time beyond the TTL window.
    state.objs[uid] = (time.time() - state.ttl - 1, obj)

    # Force the throttling check in _expire() to allow immediate expiry.
    state.last_expiry = time.time() - state.ttl
    state._expire()

    assert state.objs == {}


def test_kube_state_expire_is_throttled() -> None:
    """_expire() is a no-op when called again inside the throttle window."""
    state = PodsState(ttl=900)
    state.post({"category": "pods-state", "pods": [_pod_dict()]})
    _, obj = next(iter(state.objs.values()))
    obj.deletion_timestamp = 1

    # last_expiry was just set by post()/_update(), so the throttle window
    # (ttl / 30) has not elapsed; the stale object must survive.
    state._expire()
    assert len(state.objs) == 1


# ---------------------------------------------------------------------------
# PrometheusEventBackend — metric aggregation edge cases
# ---------------------------------------------------------------------------


def test_prometheus_backend_counts_requests_and_returns(prometheus_backend) -> None:
    """request/return events accumulate into counters instead of gauges."""
    backend = prometheus_backend
    backend.post(
        [
            {"category": "request", "template_id": "tmpl-1", "count": 3},
            {"category": "request", "template_id": "tmpl-1", "count": 2},
            {"category": "return", "count": 4},
        ]
    )
    families = {family.name: family for family in backend.collect()}
    request_counter = families["machines_requested"]
    assert request_counter.samples[0].value == 5
    return_counter = families["machines_returned"]
    assert return_counter.samples[0].value == 4


def test_prometheus_backend_ignores_request_without_template_id(
    prometheus_backend,
) -> None:
    """A 'request' event missing template_id is not counted."""
    backend = prometheus_backend
    backend.post([{"category": "request", "count": 3}])
    families = {family.name: family for family in backend.collect()}
    assert families["machines_requested"].samples == []


def test_prometheus_backend_ignores_return_without_count(prometheus_backend) -> None:
    """A 'return' event with a falsy count is not counted."""
    backend = prometheus_backend
    backend.post([{"category": "return", "count": 0}])
    families = {family.name: family for family in backend.collect()}
    assert families["machines_returned"].samples[0].value == 0


def test_prometheus_backend_ignores_metric_without_numeric_timestamp(
    prometheus_backend,
) -> None:
    """A metric event with a non-numeric/missing timestamp is dropped."""
    backend = prometheus_backend
    backend.post([{"category": "metric", "timestamp": "not-a-number", "val": 1}])
    assert backend.metrics == {}


def test_prometheus_backend_parse_event_without_string_labels(
    prometheus_backend,
) -> None:
    """_parse_event() separates numeric metrics from string labels."""
    backend = prometheus_backend
    metrics, label_names, label_values = backend._parse_event(
        {"category": "metric", "timestamp": 1.0, "val": 42, "empty_label": ""}
    )
    assert metrics == {"val": 42}
    # Empty-string labels are dropped by the `elif v:` truthiness check.
    assert label_names == ()
    assert label_values == ()


def test_prometheus_backend_close_shuts_down_server_and_thread(
    prometheus_backend,
) -> None:
    """close() shuts down the HTTP server and joins its thread when present."""
    backend = prometheus_backend
    backend.server = MagicMock()
    backend.thread = MagicMock()
    backend.close()
    backend.server.shutdown.assert_called_once()
    backend.thread.join.assert_called_once()


def test_prometheus_backend_consumes_pod_events_without_counting_as_metric(
    prometheus_backend,
) -> None:
    """A pods-state event is absorbed by the internal PodsState tracker and
    never reaches the generic metric/label parsing path."""
    backend = prometheus_backend
    backend.post([{"category": "pods-state", "pods": [_pod_dict()]}])
    assert backend.metrics == {}
    assert len(backend.pods.objs) == 1


def test_prometheus_backend_consumes_node_events_without_counting_as_metric(
    prometheus_backend,
) -> None:
    """A nodes-state event is absorbed by the internal NodesState tracker and
    never reaches the generic metric/label parsing path."""
    backend = prometheus_backend
    backend.post([{"category": "nodes-state", "nodes": [_node_dict()]}])
    assert backend.metrics == {}
    assert len(backend.nodes.objs) == 1


def test_prometheus_backend_metric_dedup_keeps_latest_timestamp(
    prometheus_backend,
) -> None:
    """An incoming metric sample does not override a stored one that is at
    least as recent."""
    backend = prometheus_backend
    now = time.time()
    backend.post([{"category": "metric", "timestamp": now, "val": 1}])
    backend.post([{"category": "metric", "timestamp": now - 10, "val": 2}])

    key = ("val", (), ())
    stored_timestamp, stored_value = backend.metrics[key]
    assert stored_timestamp == now
    assert stored_value == 1


# ---------------------------------------------------------------------------
# SqliteEventBackend
# ---------------------------------------------------------------------------


def test_sqlite_backend_skips_event_with_only_known_columns() -> None:
    """An event with no columns beyond the known set inserts nothing and
    never opens a database connection."""
    backend = SqliteEventBackend(":memory:")
    backend.post([{"category": "pod", "id": "abcd-0"}])

    assert backend.conn is None


def test_sqlite_backend_skips_state_and_metric_categories() -> None:
    """Events in 'state' or 'metric' categories are never persisted and
    never cause a database connection to be opened."""
    backend = SqliteEventBackend(":memory:")
    backend.post(
        [
            {"category": "pods-state", "pods": []},
            {"category": "metric", "timestamp": 1.0, "val": 1},
        ]
    )
    assert backend.conn is None


def test_sqlite_backend_open_creates_schema(tmp_path) -> None:
    """open() creates the parent directory and the events table/index."""
    dbfile = tmp_path / "nested" / "events.db"
    backend = SqliteEventBackend(str(dbfile), rotate=False)
    backend.open()
    try:
        assert dbfile.exists()
        with closing(backend.conn.cursor()) as cur:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            assert ("events",) in cur.fetchall()
    finally:
        backend.close()


def test_sqlite_backend_requires_a_dbfile(monkeypatch) -> None:
    """Constructing without a dbfile and no global default raises ValueError."""
    monkeypatch.setattr(context.GLOBAL, "dbfile", None)
    with pytest.raises(ValueError, match="Database file path"):
        SqliteEventBackend(dbfile=None)


def test_sqlite_backend_close_with_rotate_renames_file(tmp_path) -> None:
    """close() with rotate=True renames the dbfile out of the way."""
    dbfile = tmp_path / "events.db"
    backend = SqliteEventBackend(str(dbfile), rotate=True)
    backend.open()
    backend.close()

    assert not dbfile.exists()
    # pathlib.Path.with_suffix() replaces (not appends to) the ".db"
    # extension, so the rotated file is named "events.<timestamp_ms>".
    rotated = [p for p in tmp_path.glob("events.*") if p != dbfile]
    assert len(rotated) == 1
    assert backend.conn is None


def test_sqlite_backend_close_without_rotate_keeps_file(tmp_path) -> None:
    """close() with rotate=False leaves the dbfile in place."""
    dbfile = tmp_path / "events.db"
    backend = SqliteEventBackend(str(dbfile), rotate=False)
    backend.open()
    backend.close()

    assert dbfile.exists()
    assert backend.conn is None


def test_sqlite_backend_close_without_open_connection_is_noop(tmp_path) -> None:
    """close() is a no-op when the backend was never opened."""
    backend = SqliteEventBackend(str(tmp_path / "events.db"), rotate=True)
    backend.close()  # must not raise
    assert backend.conn is None


def test_sqlite_backend_close_with_rotate_skips_rename_when_file_missing() -> None:
    """rotate=True only renames the dbfile when it actually exists on disk
    (an in-memory database has no backing file to rotate)."""
    backend = SqliteEventBackend(":memory:", rotate=True)
    backend.post([{"category": "pod", "id": "abcd-0", "request": "r1"}])
    assert backend.conn is not None

    backend.close()  # must not raise despite dbpath.exists() being False

    assert backend.conn is None


def test_sqlite_backend_post_with_empty_events_is_noop() -> None:
    """post() with an empty list returns immediately without opening a db."""
    backend = SqliteEventBackend(":memory:")
    backend.post([])
    assert backend.conn is None


def test_sqlite_backend_post_triggers_backup_for_backup_events(tmp_path) -> None:
    """A sqlite_backup event routed through post() triggers _create_backup()."""
    dbfile = tmp_path / "events.db"
    backend = SqliteEventBackend(str(dbfile), rotate=False)
    # Open the connection and persist a row first: sqlite3's backup progress
    # callback divides by the source page count, so a backup must not be
    # attempted against a brand-new, zero-page database.
    backend.post([{"category": "pod", "id": "abcd-0", "request": "r1"}])
    backend.post([{"sqlite_backup": "via-post"}])

    backup_dir = tmp_path / "backups"
    assert list(backup_dir.glob("events_bkp_via-post_*.db"))
    backend.close()


def test_sqlite_backend_sighup_closes_connection(tmp_path) -> None:
    """sighup() closes the underlying connection like close()."""
    dbfile = tmp_path / "events.db"
    backend = SqliteEventBackend(str(dbfile), rotate=False)
    backend.open()
    backend.sighup(0, None)
    assert backend.conn is None
    assert dbfile.exists()


def test_sqlite_backend_create_backup_writes_backup_file(tmp_path) -> None:
    """_create_backup() produces a timestamped backup db under dbfile's dir."""
    dbfile = tmp_path / "events.db"
    backend = SqliteEventBackend(str(dbfile), rotate=False)
    backend.post([{"category": "pod", "id": "abcd-0", "request": "r1"}])

    # _sanitize_identifier strips anything that is not alnum/-/_ (spaces and
    # punctuation are dropped outright, not replaced).
    backend._create_backup({"sqlite_backup": "snap-1"})

    backup_dir = tmp_path / "backups"
    backups = list(backup_dir.glob("events_bkp_snap-1_*.db"))
    assert len(backups) == 1

    with closing(sqlite3.connect(backups[0])) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT COUNT(*) FROM events")
        assert cur.fetchone() == (1,)

    backend.close()


def test_sqlite_backend_create_backup_opens_connection_when_none(tmp_path) -> None:
    """_create_backup() opens its own connection and WAL-checkpoints when
    the backend has no connection yet (e.g. backup requested before any
    other event has been posted)."""
    dbfile = tmp_path / "events.db"
    # Seed the file with a table/row via an independent connection first:
    # sqlite3's backup progress callback divides by the source page count,
    # so the file must not still be a brand-new, zero-page database when
    # _create_backup() opens its own connection to it.
    with closing(sqlite3.connect(dbfile)) as seed_conn:
        seed_conn.execute("CREATE TABLE seed (x)")
        seed_conn.execute("INSERT INTO seed VALUES (1)")
        seed_conn.commit()

    backend = SqliteEventBackend(str(dbfile), rotate=False)
    assert backend.conn is None

    backend._create_backup({"sqlite_backup": "fresh"})

    assert backend.conn is not None
    backup_dir = tmp_path / "backups"
    assert list(backup_dir.glob("events_bkp_fresh_*.db"))
    backend.close()


@pytest.mark.xfail(
    strict=True,
    reason="backup progress callback divides by zero on an empty database",
)
def test_sqlite_backend_create_backup_succeeds_on_empty_database(tmp_path) -> None:
    """_create_backup() should succeed even when the source database file
    does not exist yet and has no pages (brand-new, never-written-to db)."""
    dbfile = tmp_path / "events.db"
    backend = SqliteEventBackend(str(dbfile), rotate=False)
    assert backend.conn is None
    assert not dbfile.exists()

    backend._create_backup({"sqlite_backup": "empty"})

    backup_dir = tmp_path / "backups"
    assert list(backup_dir.glob("events_bkp_empty_*.db"))
    backend.close()


def test_sqlite_backend_create_backup_logs_database_error(tmp_path, caplog) -> None:
    """A sqlite3.DatabaseError during backup is logged, not raised."""
    dbfile = tmp_path / "events.db"
    backend = SqliteEventBackend(str(dbfile), rotate=False)
    backend.post([{"category": "pod", "id": "abcd-0", "request": "r1"}])  # opens a real conn
    real_conn = backend.conn

    # sqlite3.Connection is an immutable C type: its bound methods cannot be
    # monkeypatched on the instance or the class. Swap in a stand-in
    # connection instead so `self.conn.backup(...)` raises deterministically.
    backend.conn = MagicMock()
    backend.conn.backup.side_effect = sqlite3.DatabaseError("backup failed")

    backend._create_backup({"sqlite_backup": "boom"})

    assert "Failed to create backup" in caplog.text
    backend.close()
    real_conn.close()


# ---------------------------------------------------------------------------
# _pending_events / _process_events
# ---------------------------------------------------------------------------


def test_pending_events_lists_only_files(tmp_path) -> None:
    """_pending_events() returns files, not subdirectories, from eventdir."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.json").write_text("[]")
    (tmp_path / "b.json").write_text("[]")

    files = _pending_events(tmp_path)
    assert {f.name for f in files} == {"a.json", "b.json"}


def test_process_events_wraps_single_dict_payload(tmp_path) -> None:
    """A JSON file holding a single dict (not a list) is wrapped into a list."""
    eventfile = tmp_path / "e1.json"
    eventfile.write_text(json.dumps({"category": "pod", "id": "x"}))

    received = []
    backend = MagicMock()
    backend.post.side_effect = lambda evts: received.extend(evts)

    _process_events([eventfile], [backend])

    assert received == [{"category": "pod", "id": "x"}]
    assert not eventfile.exists()


def test_process_events_handles_invalid_json(tmp_path, caplog) -> None:
    """Invalid JSON logs a warning, removes the file, and posts nothing."""
    eventfile = tmp_path / "bad.json"
    eventfile.write_text("{not valid json")

    backend = MagicMock()
    _process_events([eventfile], [backend])

    backend.post.assert_not_called()
    assert not eventfile.exists()
    assert "Invalid JSON" in caplog.text


def test_process_events_noop_when_no_files() -> None:
    """No pending files means no backend is invoked."""
    backend = MagicMock()
    _process_events([], [backend])
    backend.post.assert_not_called()


def test_process_events_still_calls_all_backends_and_reraises_last_error(
    tmp_path,
) -> None:
    """All backends receive the batch even if an earlier one raises;
    the last raised exception propagates."""
    eventfile = tmp_path / "e1.json"
    eventfile.write_text(json.dumps([{"category": "pod", "id": "x"}]))

    failing_backend = MagicMock()
    failing_backend.post.side_effect = ValueError("first backend failed")
    ok_backend = MagicMock()

    with pytest.raises(ValueError, match="first backend failed"):
        _process_events([eventfile], [failing_backend, ok_backend])

    failing_backend.post.assert_called_once()
    ok_backend.post.assert_called_once()


# ---------------------------------------------------------------------------
# _EventFileHandler
# ---------------------------------------------------------------------------


def test_event_file_handler_on_created_enqueues_file_path() -> None:
    """on_created() enqueues the created file's path when not a directory."""
    q: queue_module.Queue = queue_module.Queue()
    handler = _EventFileHandler(q)
    fake_event = MagicMock(is_directory=False, src_path="/tmp/evt1")

    handler.on_created(fake_event)

    assert q.get_nowait() == "/tmp/evt1"


def test_event_file_handler_on_created_ignores_directories() -> None:
    """on_created() ignores directory creation events."""
    q: queue_module.Queue = queue_module.Queue()
    handler = _EventFileHandler(q)
    fake_event = MagicMock(is_directory=True, src_path="/tmp/dir1")

    handler.on_created(fake_event)

    assert q.empty()


def test_event_file_handler_on_moved_enqueues_destination_path() -> None:
    """on_moved() enqueues the moved file's destination path."""
    q: queue_module.Queue = queue_module.Queue()
    handler = _EventFileHandler(q)
    fake_event = MagicMock(is_directory=False, dest_path="/tmp/evt2")

    handler.on_moved(fake_event)

    assert q.get_nowait() == "/tmp/evt2"


def test_event_file_handler_on_moved_ignores_directories() -> None:
    """on_moved() ignores directory move events."""
    q: queue_module.Queue = queue_module.Queue()
    handler = _EventFileHandler(q)
    fake_event = MagicMock(is_directory=True, dest_path="/tmp/dir2")

    handler.on_moved(fake_event)

    assert q.empty()


# ---------------------------------------------------------------------------
# _watch_events / watch — Observer and queue are mocked so the normally
# infinite watch loop terminates deterministically without real filesystem
# events, sleeps, or timing dependencies.
# ---------------------------------------------------------------------------


class _OneShotQueue:
    """Minimal queue stand-in: succeeds once (triggering a re-scan of
    pending events), is Empty once, then raises a sentinel to escape the
    infinite watch loop deterministically."""

    def __init__(self, *_args, **_kwargs) -> None:
        self._calls = 0

    def put(self, _item) -> None:
        return None

    def get(self, timeout=None):  # noqa: ARG002
        self._calls += 1
        if self._calls == 1:
            return "some/event/path"
        if self._calls == 2:
            raise queue_module.Empty
        raise RuntimeError("stop-watch-loop")


def test_watch_events_closes_backends_and_stops_observer(monkeypatch, tmp_path) -> None:
    """The loop-exit exception propagates and backends/observer are cleaned up."""
    mock_observer_instance = MagicMock()
    monkeypatch.setattr(events_impl, "Observer", MagicMock(return_value=mock_observer_instance))
    monkeypatch.setattr(events_impl.queue, "Queue", _OneShotQueue)

    backend = MagicMock()

    with pytest.raises(RuntimeError, match="stop-watch-loop"):
        _watch_events(tmp_path, [backend])

    mock_observer_instance.schedule.assert_called_once()
    mock_observer_instance.start.assert_called_once()
    mock_observer_instance.stop.assert_called_once()
    mock_observer_instance.join.assert_called_once()
    backend.close.assert_called_once()


def test_watch_events_backend_close_error_overrides_loop_exception(monkeypatch, tmp_path) -> None:
    """If closing a backend raises, that exception wins over the loop's,
    and every backend's close() is still attempted."""
    monkeypatch.setattr(events_impl, "Observer", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(events_impl.queue, "Queue", _OneShotQueue)

    failing_backend = MagicMock()
    failing_backend.close.side_effect = OSError("close failed")
    other_backend = MagicMock()

    with pytest.raises(OSError, match="close failed"):
        _watch_events(tmp_path, [failing_backend, other_backend])

    failing_backend.close.assert_called_once()
    other_backend.close.assert_called_once()


def test_watch_builds_backend_list_without_prometheus(monkeypatch, tmp_path) -> None:
    """watch() wires a console + sqlite backend when no Prometheus addr/port given."""
    monkeypatch.setattr(context.GLOBAL, "dbfile", str(tmp_path / "events.db"))
    captured_backends = {}

    def _fake_watch_events(eventdir, backends) -> None:  # noqa: ARG001
        captured_backends["backends"] = backends

    monkeypatch.setattr(events_impl, "_watch_events", _fake_watch_events)

    watch(tmp_path, prometheus_addr=None, prometheus_port=None)

    backends = captured_backends["backends"]
    assert len(backends) == 2
    assert isinstance(backends[0], ConsoleEventBackend)
    assert isinstance(backends[1], SqliteEventBackend)


def test_watch_builds_backend_list_with_prometheus(monkeypatch, tmp_path) -> None:
    """watch() appends a Prometheus backend when addr and port are supplied."""
    monkeypatch.setattr(context.GLOBAL, "dbfile", str(tmp_path / "events.db"))
    captured_backends = {}

    def _fake_watch_events(eventdir, backends) -> None:  # noqa: ARG001
        captured_backends["backends"] = backends

    monkeypatch.setattr(events_impl, "_watch_events", _fake_watch_events)
    monkeypatch.setattr(events_impl, "PrometheusEventBackend", MagicMock())

    watch(tmp_path, prometheus_addr="127.0.0.1", prometheus_port=9999)

    backends = captured_backends["backends"]
    assert len(backends) == 3
    events_impl.PrometheusEventBackend.assert_called_once_with(
        addr="127.0.0.1", port=9999, ttl=3600
    )
