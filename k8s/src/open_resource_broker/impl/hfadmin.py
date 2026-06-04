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

Implement open-resource-broker admin CLI.
"""

import logging
import random
from time import sleep

import kubernetes

from open_resource_broker import k8sutils

logger = logging.getLogger(__name__)


def get_pods_in_current_namespace() -> kubernetes.client.models.V1PodList:
    """Get the pods in the current namespace"""
    # Load Kubernetes configuration
    kubernetes.config.load_kube_config()

    # Get the current namespace
    namespace = kubernetes.config.list_kube_config_contexts()[1]["context"]["namespace"]

    # List the pods in the current namespace
    return k8sutils.get_kubernetes_client().list_namespaced_pod(namespace=namespace)


def drain_node_in_namespace(node_count=1, sleep_duration: int = 5) -> int:
    """Modeling draining a node as deleting all the pods running on a node.
    This picks a random node and delete all pods on it.
    """
    # Load Kubernetes configuration
    kubernetes.config.load_kube_config()

    # Get the current namespace
    namespace = kubernetes.config.list_kube_config_contexts()[1]["context"]["namespace"]

    # List the pods in the current namespace
    pods = (
        k8sutils.get_kubernetes_client().list_namespaced_pod(namespace=namespace).items
    )
    if len(pods) == 0:
        logger.info("There are no active pods, nothing to do")
        return 0

    # List the nodes in the current namespace we are running on
    nodes = [pod.spec.node_name for pod in pods]
    logger.info("Active nodes are %s", nodes)
    # Pick a random node
    random_nodes = random.sample(nodes, node_count)
    logger.info("Picked node %s to empty: %s", node_count, random_nodes)
    count = 0
    # Delete each pod
    for pod in pods:
        if pod.spec.node_name in random_nodes:
            logger.info("Deleting pod %s", pod.metadata.name)
            k8sutils.get_kubernetes_client().delete_namespaced_pod(
                name=pod.metadata.name, namespace=namespace
            )
            count += 1
    while len(get_pods_in_current_namespace().items) > len(pods) - count:
        sleep(sleep_duration)
        logger.info("Waiting for pods to be removed from drained node")
    return count


def delete_pods_in_namespace(pod_count: int = 0, sleep_duration: int = 5) -> None:
    """Clean up the namespace we are evolving in"""
    # Load Kubernetes configuration
    kubernetes.config.load_kube_config()

    # Get the current namespace
    namespace = kubernetes.config.list_kube_config_contexts()[1]["context"]["namespace"]

    # List the pods in the current namespace
    pods = (
        k8sutils.get_kubernetes_client().list_namespaced_pod(namespace=namespace).items
    )
    logger.info("Pods types is %s", type(pods))

    random_pods = pods if pod_count == 0 else random.sample(pods, pod_count)

    target_count = len(pods) - pod_count

    # Delete each pod
    for pod in random_pods:
        k8sutils.get_kubernetes_client().delete_namespaced_pod(
            name=pod.metadata.name, namespace=namespace
        )
    while len(get_pods_in_current_namespace().items) > target_count:
        sleep(sleep_duration)
        logger.info(
            "Waiting for namespace to clean up from deleted pods, sleeping %s s",
            sleep_duration,
        )
