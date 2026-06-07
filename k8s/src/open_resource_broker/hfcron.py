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

Cleanup/GC process to remove timed out machine requests and pods.
"""

import logging
import pathlib
import time
from collections.abc import Callable
from datetime import datetime
from http import HTTPStatus
from random import randint

import kubernetes

from open_resource_broker import events as hfevents
from open_resource_broker import fsutils
from open_resource_broker import k8sutils

logger = logging.getLogger(__name__)


class CleanupPodsTask:
    """Remove timed out pods"""

    def __init__(
        self: "CleanupPodsTask",
        k8s_client,
        workdir,
        pod_timeout,
        interval=30,
        dry_run=False,
    ) -> None:
        """init pod timeout task"""
        self.pod_timeout = pod_timeout
        self.k8s_client = k8s_client
        self.interval = interval
        self.podstatusdir = workdir / "pods-status"
        self.namespace = k8sutils.get_namespace()
        self.dry_run = dry_run
        self.deadline = time.time() + randint(0, 15)  # noqa: S311

    def _is_timeout_reached(
        self: "CleanupPodsTask",
        pod_ctime: datetime,
    ) -> bool:
        """Check if the timeout is reached"""
        current_time = datetime.now()
        elapsed_time = current_time - pod_ctime
        return elapsed_time.total_seconds() > self.pod_timeout

    # FIXIT: duplicate code exists in watcher.
    # This should probably live in k8sutils
    def _delete_k8s_pod(self: "CleanupPodsTask", pod_name: str) -> None:
        """Delete a k8s pod"""
        try:
            self.k8s_client.delete_namespaced_pod(
                pod_name, self.namespace, body=kubernetes.client.V1DeleteOptions()
            )
        except kubernetes.client.rest.ApiException as exc:
            if exc.status == HTTPStatus.NOT_FOUND:
                logger.exception("Pod not found: %s", pod_name)
                return

        logger.info("Deleted pod: %s", pod_name)

    def __call__(self: "CleanupPodsTask") -> None:
        """run the pods cleanup"""
        logger.info("Cleaning up pods")

        with hfevents.EventsBuffer() as events:
            for pod in fsutils.iterate_directory(
                directory=self.podstatusdir, symlinks_only=True
            ):
                pod_ctime = datetime.fromtimestamp(pod.lstat().st_ctime)
                pod_status = pod.readlink().name

                if pod_status in [
                    "creating",
                    "pending",
                ] and self._is_timeout_reached(pod_ctime):
                    logger.info("Pod %s timed out.", pod.name)

                    events.post(
                        category="pod",
                        id=pod.name,
                        status="timeout",
                    )

                    if self.dry_run:
                        logger.info("Dry-run: Would have deleted pod %s", pod.name)
                        continue

                    self._delete_k8s_pod(pod.name)
                    fsutils.atomic_symlink("deleted", pod)

        self.deadline = time.time() + self.interval


class RefreshNodesTask:
    """Refresh node list task"""

    def __init__(
        self: "RefreshNodesTask",
        k8s_client,
        workdir,
        interval=300,
        dry_run=False,
    ) -> None:
        """init node refresh"""
        self.interval = interval
        self.deadline = time.time() + randint(0, 15)  # noqa: S311
        self.k8s_client = k8s_client
        self.nodesdir = workdir / "nodes"
        self.dry_run = dry_run

    # FIXIT: This should probably live in k8sutils
    def __call__(self: "RefreshNodesTask") -> None:
        """Get the list of nodes in cluster and save it in /nodes"""
        logger.info("Refreshing nodes")

        expected_nodes = {
            node.name
            for node in fsutils.iterate_directory(
                directory=self.nodesdir,
                files_only=True,
            )
        }

        try:
            actual_nodes = set()
            nodes_state = []
            for node in self.k8s_client.list_node().items:
                node_name = node.metadata.name

                if not self.dry_run:
                    fsutils.atomic_write(node, self.nodesdir / node_name)
                    nodes_state.append(node)
                actual_nodes.add(node_name)
                if node_name not in expected_nodes:
                    logger.info("Adding new node: %s", node)

            for node in expected_nodes - actual_nodes:
                logger.info("Removing stale node: %s", node)
                if not self.dry_run:
                    (self.nodesdir / node).unlink(missing_ok=True)

            if not self.dry_run:
                with hfevents.EventsBuffer() as events:
                    events.post(
                        category="nodes-state",
                        nodes=nodes_state,
                    )
        except kubernetes.client.rest.ApiException as exc:
            logger.exception("Could not get list of nodes: %s", exc)

        self.deadline = time.time() + self.interval


class RefreshPodsTask:
    """Refresh pod list task"""

    def __init__(
        self: "RefreshPodsTask",
        k8s_client,
        interval=180,
        dry_run=False,
    ) -> None:
        """init pod list"""
        self.interval = interval
        self.deadline = time.time() + randint(0, 15)  # noqa: S311
        self.k8s_client = k8s_client
        self.namespace = k8sutils.get_namespace()
        self.dry_run = dry_run

    def __call__(self: "RefreshPodsTask") -> None:
        """Get the list of pods and generate metrics for them"""
        logger.info("Refreshing pods")

        try:
            with hfevents.EventsBuffer() as events:
                response = self.k8s_client.list_namespaced_pod(self.namespace)
                events.post(
                    category="pods-state",
                    pods=tuple(response.items),
                )
        except kubernetes.client.rest.ApiException as exc:
            logger.exception("Could not get list of pods: %s", exc)

        self.deadline = time.time() + self.interval


def run(
    k8s_client,
    workdir,
    pod_timeout,
    run_once=False,
    dry_run=False,
    pod_cleanup_interval=30,
    node_refresh_interval=300,
) -> None:
    """Run the cleanup process"""
    workdir = pathlib.Path(workdir)

    tasks: tuple[Callable] = (
        CleanupPodsTask(
            k8s_client,
            workdir,
            pod_timeout,
            interval=pod_cleanup_interval,
            dry_run=dry_run,
        ),
        RefreshPodsTask(
            k8s_client,
            dry_run=dry_run,
        ),
        RefreshNodesTask(
            k8s_client,
            workdir,
            interval=node_refresh_interval,
            dry_run=dry_run,
        ),
    )

    if run_once:
        for task in tasks:
            task()
    else:
        while True:
            queue = sorted(tasks, key=lambda task: task.deadline)
            next_task = queue[0]
            time_remaining = next_task.deadline - time.time()
            if time_remaining < 3:  # noqa: PLR2004
                next_task()
            else:
                logger.info(
                    "Waiting %s secs for the next task", round(time_remaining, 1)
                )
                time.sleep(time_remaining)
