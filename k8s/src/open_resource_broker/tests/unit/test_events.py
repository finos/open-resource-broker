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
import tempfile
import time
from contextlib import closing

import pytest

from open_resource_broker import events
from open_resource_broker import fsutils
from open_resource_broker.cli import context
from open_resource_broker.impl.watchers.events import PrometheusEventBackend
from open_resource_broker.impl.watchers.events import SqliteEventBackend


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


def test_prometheus_events_backend() -> None:
    """Test events with prometheus."""
    now = time.time()
    backend = PrometheusEventBackend(port=None, ttl=1000)

    try:
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
    finally:
        backend.close()
