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

Process and collect open-resource-broker events.

This module will collect pod and nodes events and store them in a SQLite
database.

TODO: Should we consider jaeger/open-telemetry for tracing? Probably, but the
      immediate goal is to collect and store stats about requests and to be
      able to compare them with subsequent runs.
"""

import logging
import os
import pathlib
from time import time
from typing import Any
from uuid import uuid4

from open_resource_broker import fsutils
from open_resource_broker.cli import context

logger = logging.getLogger(__name__)


def _extract_metadata(src) -> dict[str, str]:
    metadata = {}
    prefix = "HF_K8S_METADATA_"
    for k, v in src.items():
        if k.startswith(prefix):
            suffix = k[len(prefix) :]
            if suffix:
                metadata[suffix.lower()] = v
    return metadata


EXTRA_METADATA = _extract_metadata(os.environ)


# We are batching events mostly for two reasons:
# - Reduce the number of files created and the related I/O and inotifications.
# - Batches are highly beneficial for remote backends (i.e. Cloudwatch,
#   Elasticsearch, etc). Code is structured to facilitate other backends.
# Batches however are unpleasant to deal with because the caller must remember
# about flushing the buffered data, which is prone to error.
# Context manager addresses the problem.
class EventsBuffer:
    """Context manager for batched event push"""

    def __init__(self: "EventsBuffer") -> None:
        """Prepare a fresh events buffer"""
        self.events: list[dict[str, Any]] = []

    def _flush(self: "EventsBuffer") -> None:
        if self.events:
            filename = f"{uuid4()}"
            fsutils.atomic_write(
                [EXTRA_METADATA | event for event in self.events],
                pathlib.Path(context.GLOBAL.dirname) / filename,
            )
            self.events = []

    def _buffer(self: "EventsBuffer", *events: list[dict[str, Any]]) -> None:
        timestamp = None
        for event in events:
            assert isinstance(event, dict)  # noqa: S101
            if event.get("timestamp") is None:
                if timestamp is None:
                    timestamp = int(time())
                event["timestamp"] = timestamp
            self.events.append(event)

    def __enter__(self: "EventsBuffer") -> "EventsBuffer":
        """Enter the context manager"""
        assert not self.events  # noqa: S101
        return self

    def __exit__(self: "EventsBuffer", exc_type, exc_val, exc_tb) -> bool | None:
        """Exit the context manager"""
        self._flush()
        return None

    def post(self: "EventsBuffer", *args: list[Any], **kwargs: dict[str, Any]) -> None:
        """Buffer event(s)."""
        if args:
            assert not kwargs  # noqa: S101
            self._buffer(*args)
        else:
            assert kwargs  # noqa: S101
            self._buffer(kwargs)
