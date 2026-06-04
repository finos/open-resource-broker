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

Watches nodes events are writes them to the database
"""

import json
import logging
import pathlib

from open_resource_broker import events as hfevents
from open_resource_broker import k8sutils
from open_resource_broker.impl.watchers import cluster

logger = logging.getLogger(__name__)


def watch(workdir: pathlib.Path) -> None:
    """Watch for kubernetes events."""
    k8sutils.watch_kube_events(
        workdir=workdir,
        _postprocess_event=_postprocess_event,
        _event_path=_event_path,
        field_selector="involvedObject.kind=Node",
        handler=cluster.handle_event,
    )


def _event_path(workdir: pathlib.Path, data: dict) -> pathlib.Path:
    """If None is returned, event should not be saved"""
    logger.debug("No event path for event %s in %s", data, workdir)
    return None


def _postprocess_event(workdir: pathlib.Path, event: dict) -> None:
    """Push kubernetes event to db."""
    logger.debug("Processing kube event %s in workdir %s", event, workdir)
    data = event["object"]

    involved_object = data.involved_object
    if involved_object.kind != "Node":
        logger.warning("Skipping event %s that is not for node.", data.metadata.name)
        return

    node_id = f"{involved_object.name}::{involved_object.uid}"

    with hfevents.EventsBuffer() as events:
        parsed_node_event = k8sutils.parse_node_event(data)
        if not parsed_node_event:
            return
        if parsed_node_event["message"] == "Disrupting Node: Underutilized/Delete":
            events.post(
                category="node",
                id=node_id,
                eviction_uderutilized=str(parsed_node_event["timestamp"]),
            )
        if parsed_node_event["message"] == "Disrupting Node: Empty/Delete":
            events.post(
                category="node",
                id=node_id,
                eviction_empty=str(parsed_node_event["timestamp"]),
            )
        events.post(
            category="node",
            id=node_id,
            events=json.dumps(parsed_node_event),
        )
