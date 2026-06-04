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

Common k8s helper functions.
"""

import functools
import logging
import os
import pathlib
from datetime import datetime
from http import HTTPStatus

import kubernetes
import urllib3
from tenacity import before_sleep_log
from tenacity import retry
from tenacity import retry_if_exception_type
from tenacity import retry_if_result
from tenacity import stop_after_attempt
from tenacity import wait_exponential

from open_resource_broker.cli import context

logger = logging.getLogger(__name__)

K8S_RESOURCE_VERSION_MISMATCH_CODE = HTTPStatus.GONE
RETRYABLE_HTTP_ERROR_CODES = (
    HTTPStatus.REQUEST_TIMEOUT,
    HTTPStatus.TOO_EARLY,
    HTTPStatus.TOO_MANY_REQUESTS,
    HTTPStatus.INTERNAL_SERVER_ERROR,
    HTTPStatus.NOT_IMPLEMENTED,
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT,
)
RETRYABLE_EXCEPTIONS = (
    kubernetes.client.exceptions.ApiException,
    urllib3.exceptions.ReadTimeoutError,
)


def _proxy_url() -> str:
    """Get the proxy URL from the environment.

    Returns:
        str: The proxy URL.
    """
    return os.environ.get("HTTP_PROXY", os.environ.get("http_proxy", None))


def is_inside_pod() -> bool:
    """Check if the code is running inside a Kubernetes pod.

    Returns:
        bool: True if running inside a pod, False otherwise.
    """
    return pathlib.Path("/var/run/secrets/kubernetes.io").exists()


@functools.lru_cache(maxsize=1)
def get_kubernetes_client() -> kubernetes.client.CoreV1Api:
    """Get the Kubernetes client. We cache this result to avoid
    re-creating the client multiple times.
    Returns:
        kubernetes.client.CoreV1Api: The Kubernetes client.
    """
    return kubernetes.client.CoreV1Api()


@retry(
    wait=wait_exponential(),
    stop=stop_after_attempt(5),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def load_k8s_config(proxy_url: str | None = None) -> None:
    """Load Kubernetes Credentials."""
    if is_inside_pod():
        # From Inside a Pod
        logger.debug("Loading cluster credentials.")
        kubernetes.config.load_incluster_config()
    else:
        # Local kubeconfig file
        logger.debug("Loading local credentials.")
        kubernetes.config.load_kube_config()

        # Proxy is set only for local config case. When running inside a pod,
        # proxy is not needed.
        if not proxy_url:
            proxy_url = _proxy_url()

        if proxy_url:
            logger.info("Setting proxy: %s", proxy_url)
            kubernetes.client.Configuration._default.proxy = proxy_url  # noqa SLF001


def get_namespace() -> str:
    """Retrieves the namespace based on the environment.

    Returns:
        str: The namespace to be used.
    """
    if is_inside_pod():
        namespace = (
            pathlib.Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
            .read_text()
            .strip()
        )
    else:
        # TODO: This assumes single namespace. Better solution to be
        #       explicit and require namespace to be set in the CLI opts.
        namespace = kubernetes.config.list_kube_config_contexts()[1]["context"][
            "namespace"
        ]

    return namespace


def watch_pods(
    label_selector,
    handler,
    namespace,
    workdir: pathlib.Path,
    _postprocess_event,
    _event_path,
) -> None:
    """Watch for pods based on label selector with per-cycle retry reset."""
    resource_version = "0"
    while True:
        resource_version = _watch_events(
            get_kubernetes_client().list_namespaced_pod,
            handler,
            workdir,
            _postprocess_event,
            _event_path,
            resource_version,
            namespace=namespace,
            label_selector=label_selector,
            timeout_seconds=context.GLOBAL.kube_server_timeout_seconds,
        )


def watch_nodes(
    label_selector, handler, workdir: pathlib.Path, _postprocess_event, _event_path
) -> None:
    """Watch for nodes based on label selector with per-cycle retry reset."""
    resource_version = "0"
    while True:
        resource_version = _watch_events(
            get_kubernetes_client().list_node,
            handler,
            workdir,
            _postprocess_event,
            _event_path,
            resource_version,
            label_selector=label_selector,
            timeout_seconds=context.GLOBAL.kube_server_timeout_seconds,
        )


def watch_kube_events(
    field_selector, handler, workdir: pathlib.Path, _postprocess_event, _event_path
) -> None:
    """Watch for Kubernetes events with per-cycle retry reset."""
    resource_version = "0"
    while True:
        resource_version = _watch_events(
            get_kubernetes_client().list_event_for_all_namespaces,
            handler,
            workdir,
            _postprocess_event,
            _event_path,
            resource_version,
            field_selector=field_selector,
            timeout_seconds=context.GLOBAL.kube_server_timeout_seconds,
        )


@retry(
    wait=wait_exponential(),
    stop=stop_after_attempt(5),
    retry=(
        retry_if_exception_type(RETRYABLE_EXCEPTIONS)
        | retry_if_result(lambda resp: resp in RETRYABLE_HTTP_ERROR_CODES)
    ),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def _watch_events(
    api_call,
    handler,
    workdir: pathlib.Path,
    _postprocess_event,
    _event_path,
    resource_version: str,
    **kwargs: dict,
) -> str:
    """Perform a single watch stream cycle."""
    kwargs["resource_version"] = resource_version
    stream = kubernetes.watch.Watch().stream(api_call, **kwargs)

    try:
        for event in stream:
            handler(workdir, _postprocess_event, _event_path, event)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status != K8S_RESOURCE_VERSION_MISMATCH_CODE:
            raise
        obj = event["object"] if isinstance(event, dict) and "object" in event else None
        rv = "0"
        if obj is not None and getattr(obj, "metadata", None):
            rv = getattr(obj.metadata, "resource_version", None) or "0"
        resource_version = rv
        logger.warning(
            "Restarting watcher due to resourceVersion mismatch. "
            "Using resourceVersion: %s",
            resource_version,
        )
        return resource_version
    except urllib3.exceptions.ProtocolError as exc:
        logger.warning(
            "Protocol error (%s). Soft restart from resource_version=%s",
            exc,
            resource_version,
        )
        return resource_version
    except Exception:
        raise

    logger.warning(
        "End of events stream. Soft restart from resource_version=%s", resource_version
    )
    return resource_version


def _parse_cpu_quantity(quantity: str) -> float:
    """Parse the CPU quantity."""
    if not quantity:
        return 0.0
    if quantity.endswith("m"):
        return int(quantity[:-1]) / 1000  # Convert millicores to cores
    return float(quantity)  # Assume the quantity is already in cores


def _parse_memory_quantity(quantity: str) -> int:  # noqa: PLR0911
    """Parse the memory quantity."""
    if not quantity:
        return 0
    if quantity.endswith("Ki"):
        return int(quantity[:-2]) * 1024  # Convert Ki to bytes
    if quantity.endswith("Mi"):
        return int(quantity[:-2]) * 1024 * 1024  # Convert Mi to bytes
    if quantity.endswith("Gi"):
        return int(quantity[:-2]) * 1024 * 1024 * 1024  # Convert Gi to bytes
    if quantity.endswith("k"):
        return int(quantity[:-1]) * 1000
    if quantity.endswith("M"):
        return int(quantity[:-1]) * 1000000
    if quantity.endswith("G"):
        return int(quantity[:-1]) * 1000000000
    return int(quantity)  # Assume the quantity is already in bytes


def get_total_pod_memory(pod) -> tuple[float, float]:
    """Get the total memory of a pod.

    Args:
        pod (kubernetes.client.V1Pod): The pod object.

    Returns:
            tuple[float, float]: The total memory request and limit in MiB.
    """
    total_memory_request_bytes = 0
    total_memory_limit_bytes = 0

    if pod.spec.containers:
        for container in pod.spec.containers:
            if (
                container.resources.requests
                and "memory" in container.resources.requests
            ):
                total_memory_request_bytes += _parse_memory_quantity(
                    container.resources.requests["memory"]
                )
            if container.resources.limits and "memory" in container.resources.limits:
                total_memory_limit_bytes += _parse_memory_quantity(
                    container.resources.limits["memory"]
                )

    return (
        round((total_memory_request_bytes / (1024 * 1024)), 2),
        round((total_memory_limit_bytes / (1024 * 1024)), 2),
    )


def get_total_pod_cpu(pod) -> tuple[float, float]:
    """Get the total CPU of a pod.

    Args:
        pod (kubernetes.client.V1Pod): The pod object.

    Returns:
            tuple[float, float]: The total CPU request and limit in cores.
    """
    total_cpu_request = 0
    total_cpu_limit = 0

    if pod.spec.containers:
        for container in pod.spec.containers:
            if container.resources.requests and "cpu" in container.resources.requests:
                total_cpu_request += _parse_cpu_quantity(
                    container.resources.requests["cpu"]
                )
            if container.resources.limits and "cpu" in container.resources.limits:
                total_cpu_limit += _parse_cpu_quantity(
                    container.resources.limits["cpu"]
                )

    return (round(total_cpu_request, 2), round(total_cpu_limit, 2))


def get_pod_container_statuses(pod) -> dict:
    """Get the container statuses of a pod.

    Args:
        pod (kubernetes.client.V1Pod): The pod object.

    Returns:
        dict: The container statuses.
    """
    container_statuses = {}
    if pod.status.container_statuses:
        for status in pod.status.container_statuses:
            container_status = {
                "ready": status.ready,
                "started": status.started,
            }
            if status.state.running:
                container_status["state"] = "Running"
            elif status.state.terminated:
                container_status["state"] = "Terminated"
                container_status["exit_code"] = status.state.terminated.exit_code
                container_status["reason"] = status.state.terminated.reason
            elif status.state.waiting:
                container_status["state"] = "Waiting"
                container_status["reason"] = status.state.waiting.reason

            container_statuses[status.name] = container_status
    return container_statuses


class Pod:
    """Dataclass for pod objects used in pods state"""

    def __init__(self: "Pod", obj) -> None:
        """init the pod from either API object or plain dict"""
        try:  # noqa: SIM105
            obj = obj.to_dict()
        except AttributeError:
            pass

        metadata = obj["metadata"]
        spec = obj["spec"]
        status = obj["status"]
        labels = metadata["labels"]
        self.uid = metadata["uid"]
        self.pod_name = metadata["name"]
        self.pod_type = labels.get("app.kubernetes.io/name", "")
        self.namespace = metadata["namespace"]
        self.creation_timestamp = metadata["creation_timestamp"]
        self.deletion_timestamp = metadata.get("deletion_timestamp")
        self.version = int(metadata["resource_version"])
        self.node_name = spec["node_name"]
        self.node_ip = status.get("host_ip")
        self.pod_ip = status.get("pod_ip")
        self.phase = status["phase"].lower()
        self.start_timestamp = status.get("start_time", 0)
        if self.start_timestamp:
            self.start_time = datetime.fromtimestamp(self.start_timestamp).isoformat()
        else:
            self.start_time = ""
        if self.deletion_timestamp:
            self.end_time = datetime.fromtimestamp(self.deletion_timestamp).isoformat()
        else:
            self.end_time = ""
        self.cpu = 0.0
        self.memory = 0
        self.image = ""
        self.restart_count = 0
        for container in spec.get("containers", ()):
            if not self.image:
                self.image = container.get("image", "")
            resources = container["resources"]
            resources = resources.get("requests") or resources.get("limits") or {}
            self.cpu += _parse_cpu_quantity(resources.get("cpu"))
            self.memory += _parse_memory_quantity(resources.get("memory"))
        for container_status in status.get("container_statuses") or ():
            self.restart_count += container_status.get("restart_count", 0)
        self.memory = self.memory / 1048576
        self.conditions = status.get("conditions") or ()


class Node:
    """Dataclass for node objects used in nodes state"""

    def __init__(self: "Node", obj) -> None:
        """init the node from either API object or plain dict"""
        try:  # noqa: SIM105
            obj = obj.to_dict()
        except AttributeError:
            pass

        metadata = obj["metadata"]
        status = obj["status"]
        labels = metadata["labels"]
        self.uid = metadata["uid"]
        self.node_name = metadata["name"]
        self.creation_timestamp = metadata["creation_timestamp"]
        self.deletion_timestamp = metadata.get("deletion_timestamp")
        self.instance_type = labels.get("node.kubernetes.io/instance-type", "")
        self.capacity_type = (
            (
                labels.get("karpenter.sh/capacity-type", "")
                or labels.get("eks.amazonaws.com/capacityType", "")
            )
            .lower()
            .replace("_", "-")
        )
        self.zone_id = labels.get("topology.k8s.aws/zone-id", "")
        self.version = int(metadata["resource_version"])
        self.node_ip = ""
        for addr in status.get("addresses", ()):
            if self.node_ip:
                break
            if addr.get("type") == "InternalIP":
                self.node_ip = addr.get("address", "")
        if self.creation_timestamp:
            self.start_time = datetime.fromtimestamp(
                self.creation_timestamp
            ).isoformat()
        else:
            self.start_time = ""
        if self.deletion_timestamp:
            self.end_time = datetime.fromtimestamp(self.deletion_timestamp).isoformat()
        else:
            self.end_time = ""
        resources = status.get("capacity") or status.get("allocatable") or {}
        self.cpu = _parse_cpu_quantity(resources.get("cpu"))
        self.memory = _parse_memory_quantity(resources.get("memory")) / 1048576
        self.conditions = status.get("conditions") or ()


def get_node_conditions(node: kubernetes.client.V1Node) -> dict:
    """Get the conditions of a node.

    Args:
        node (kubernetes.client.V1Node): The node object.

    Returns:
        dict: The node conditions.
    """
    node_conditions = {}
    if node.status.conditions:
        for condition in node.status.conditions:
            node_conditions[condition.type] = {
                "status": condition.status,
                "reason": condition.reason,
                "message": condition.message,
            }
    return node_conditions


def get_node_memory_resources(node: kubernetes.client.V1Node) -> dict:
    """Get the memory resources of a node.

    Args:
        node (kubernetes.client.V1Node): The node object.

    Returns:
        dict: The memory capacity, allocatable and reserved in MiB.
    """
    memory_capacity_bytes = _parse_memory_quantity(node.status.capacity["memory"])
    memory_allocatable_bytes = _parse_memory_quantity(node.status.allocatable["memory"])
    memory_reserved_bytes = memory_capacity_bytes - memory_allocatable_bytes

    return {
        "capacity": round((memory_capacity_bytes / (1024 * 1024)), 2),
        "allocatable": round((memory_allocatable_bytes / (1024 * 1024)), 2),
        "reserved": round((memory_reserved_bytes / (1024 * 1024)), 2),
    }


def get_node_cpu_resources(node: kubernetes.client.V1Node) -> dict:
    """Get the CPU resources of a node.

    Args:
        node (kubernetes.client.V1Node): The node object.

    Returns:
        dict: The CPU capacity, allocatable and reserved in cores.
    """
    cpu_capacity = _parse_cpu_quantity(node.status.capacity["cpu"])
    cpu_allocatable = _parse_cpu_quantity(node.status.allocatable["cpu"])
    cpu_reserved = cpu_capacity - cpu_allocatable

    return {
        "capacity": round(cpu_capacity, 2),
        "allocatable": round(cpu_allocatable, 2),
        "reserved": round(cpu_reserved, 2),
    }


def parse_node_event(data) -> dict | None:
    """Parse a node event.

    Args:
        data (kubernetes.client.V1Event): The event object.

    Returns:
        dict: The parsed node event.
    """
    involved_object = data.involved_object
    if involved_object.name == involved_object.uid:
        logger.warning("Skipping event %s with missing object uid.", data.metadata.name)
        return None
    if involved_object.kind != "Node":
        logger.warning("Skipping event %s that is not for node.", data.metadata.name)
        return None

    logger.debug("Parsing node event: %s", data)
    return {
        "type": data.type,
        "reason": data.reason,
        "message": data.message,
        "source": data.reporting_component,
        "timestamp": int(data.metadata.creation_timestamp.timestamp()),
    }


@retry(
    wait=wait_exponential(),
    stop=stop_after_attempt(5),
    retry=(
        retry_if_exception_type(RETRYABLE_EXCEPTIONS)
        | retry_if_result(lambda resp: resp in RETRYABLE_HTTP_ERROR_CODES)
    ),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def list_node() -> list[dict]:
    """Get the list of nodes in cluster

    Returns:
        list[dict]: The list of node objects
    """
    return get_kubernetes_client().list_node().items
