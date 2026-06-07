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

import logging
import pathlib

from open_resource_broker import events as hfevents
from open_resource_broker import k8sutils
from open_resource_broker.impl.watchers import cluster

logger = logging.getLogger(__name__)


def watch(workdir: pathlib.Path) -> None:
    """Watch for node events."""
    k8sutils.watch_nodes(
        workdir=workdir,
        _postprocess_event=_postprocess_event,
        _event_path=_event_path,
        label_selector=None,
        handler=cluster.handle_event,
    )


def _event_path(workdir: pathlib.Path, data: dict) -> pathlib.Path:
    """If None is returned, event should not be saved"""
    return workdir / "nodes" / data.metadata.name


def _postprocess_event(workdir: pathlib.Path, event: dict) -> None:
    """Push node event to db."""
    logger.debug("Processing node event: %s in workdir %s", event, workdir)
    data = event["object"]
    node_id = f"{data.metadata.name}::{data.metadata.uid}"

    with hfevents.EventsBuffer() as events:
        events.post(
            category="node",
            id=node_id,
            event=event,
        )

        if data.metadata.creation_timestamp:
            events.post(
                category="node",
                id=node_id,
                status="created",
            )

        if data.metadata.deletion_timestamp:
            events.post(
                category="node",
                id=node_id,
                status="deleted",
            )

        for condition in data.status.conditions or ():
            if condition.type == "Ready" and condition.status == "True":
                events.post(
                    category="node",
                    id=node_id,
                    status="ready",
                )

        node_conditions = k8sutils.get_node_conditions(data)
        if node_conditions:
            events.post(category="node", id=node_id, conditions=node_conditions)

        cpu_parameters = k8sutils.get_node_cpu_resources(data)
        memory_parameters = k8sutils.get_node_memory_resources(data)

        events.post(
            category="node",
            id=node_id,
            cpu_capacity=str(cpu_parameters.get("capacity")),
            cpu_allocatable=str(cpu_parameters.get("allocatable")),
            memory_capacity=str(memory_parameters.get("capacity")),
            memory_allocatable=str(memory_parameters.get("allocatable")),
            cpu_reserved=str(cpu_parameters.get("reserved")),
            memory_reserved=str(memory_parameters.get("reserved")),
            zone=data.metadata.labels.get("topology.kubernetes.io/zone"),
            region=data.metadata.labels.get("topology.kubernetes.io/region"),
            node_size=data.metadata.labels.get("node.kubernetes.io/instance-type"),
            capacity_type=data.metadata.labels.get("karpenter.sh/capacity-type"),
        )
