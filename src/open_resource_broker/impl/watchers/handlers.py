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

Handlers for Hostfactory watchers
"""

import http
import json
import logging
import pathlib
import uuid
from typing import Any

import kubernetes
import yaml

from open_resource_broker import fsutils
from open_resource_broker import k8sutils
from open_resource_broker.cli import context

logger = logging.getLogger(__name__)


def _generate_event_json(filename: pathlib.Path) -> dict[str, Any]:
    """Generate event JSON from file"""
    event_type = filename.name
    category = filename.parent.name.split(".")[2]
    request_id = filename.parent.name.split(".")[-1]

    try:
        if filename.stat().st_size == 0:
            logger.warning("Empty file found: %s", filename)
            event_message = {}
        else:
            with filename.open("r", encoding="utf-8") as file:
                event_message = json.load(file)
    except json.JSONDecodeError as e:
        logger.error("Failed to decode JSON from file %s: %s", filename, e)
        return None

    return {
        "category": category,
        "id": request_id,
        "timestamp": int(filename.stat().st_mtime),
        event_type: event_message,
    }


def _post_event(workdir, event: dict[str, Any]) -> None:
    """push event to events directory"""
    events_dir = pathlib.Path(workdir) / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4()}"
    fsutils.atomic_write(
        event,
        pathlib.Path(context.GLOBAL.dirname) / filename,
    )


def _create_pod(k8s_client: kubernetes.client.CoreV1Api, machine: pathlib.Path) -> None:
    """Create pod."""
    pod_name = machine.name
    logger.info("Creating pod: %s", pod_name)
    namespace = k8sutils.get_namespace()
    with machine.open("r") as file:
        logger.info("Pod spec file is: %s", file)
        pod_tpl = yaml.safe_load(file)

    req_id = machine.parent.name
    pod_tpl["metadata"]["name"] = pod_name
    pod_tpl["metadata"]["labels"]["app"] = pod_name
    pod_tpl["metadata"]["labels"]["symphony/open-resource-broker-reqid"] = req_id

    # TODO: Handle pod creation exceptions
    logger.info("Calling k8s API to create pod: %s", pod_name)
    result = k8s_client.create_namespaced_pod(namespace=namespace, body=pod_tpl)
    logger.info("Result of pod creation: %s", result)


def _delete_pod(k8s_client: kubernetes.client.CoreV1Api, pod_name: str) -> None:
    """Delete pod."""
    logger.info("Deleting pod with client: %s", pod_name)
    logger.info("instance type %s", type(k8s_client))
    namespace = k8sutils.get_namespace()
    try:
        k8s_client.delete_namespaced_pod(pod_name, namespace)
        logger.info("Deleted pod with client: %s", pod_name)
    except kubernetes.client.rest.ApiException as exc:
        if exc.status == http.HTTPStatus.NOT_FOUND:
            # Assume the pod is already deleted if not found
            logger.info("Pod not found: %s", pod_name)
            return
        logger.info("Failed deleting the pod %s with %s", pod_name, exc)
        raise exc


def make_request_machine_handler(k8s_client: kubernetes.client.CoreV1Api) -> callable:
    """Create a handler for request machine."""

    def handle_request_machine(workdir: str, machine: pathlib.Path) -> None:
        """Create machine."""
        logger.info("Create machine: %s from workdir %s", machine, workdir)
        # Initially, machine is a symlink to a podspec file.
        # If it is not, that means it's been already processed.
        # Which means, we rerun a partially processed request dir.
        if machine.is_symlink():
            _create_pod(k8s_client, machine)
            # Flip the softlink into an empty file to mark it's done with.
            fsutils.atomic_create_empty(machine)
        else:
            logger.info("%s is not a symlink, skipping", machine)

    return handle_request_machine


def make_return_machine_handler(k8s_client: kubernetes.client.CoreV1Api) -> callable:
    """Create a handler for return machine."""

    def handle_return_machine(workdir: str, machine: pathlib.Path) -> None:
        """Return machine."""
        pod_name = machine.name
        pod_status = fsutils.fetch_pod_status(workdir, pod_name)
        logger.info("Returning machine: %s with status %s", pod_name, pod_status)

        if pod_status is None:
            logger.info("Unknown pod, skipping: %s", pod_name)
            return

        if pod_status == "deleted":
            logger.info("Pod already deleted: %s", pod_name)
            return

        _delete_pod(k8s_client, pod_name)

    return handle_return_machine


def handle_request_io(workdir: str, request: pathlib.Path) -> None:
    """Process Request I/O data."""
    logger.info("Processing %s request for %s", request.name, request.parent.name)
    _post_event(workdir, _generate_event_json(request))
