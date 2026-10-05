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

Test k8sutils helper functions.
"""

from http import HTTPStatus
from unittest import mock

import kubernetes
import pytest
import urllib3

from orb.k8s_legacy import k8sutils


class TestProxyUrl:
    """Validate _proxy_url environment lookups."""

    def test_proxy_url_from_upper_case_env(self, monkeypatch) -> None:
        """Reads HTTP_PROXY first."""
        monkeypatch.setenv("HTTP_PROXY", "http://upper:8080")
        monkeypatch.setenv("http_proxy", "http://lower:8080")
        assert k8sutils._proxy_url() == "http://upper:8080"

    def test_proxy_url_falls_back_to_lower_case_env(self, monkeypatch) -> None:
        """Falls back to lower case http_proxy when upper is unset."""
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.setenv("http_proxy", "http://lower:8080")
        assert k8sutils._proxy_url() == "http://lower:8080"

    def test_proxy_url_missing(self, monkeypatch) -> None:
        """Returns None when neither variable is set."""
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("http_proxy", raising=False)
        assert k8sutils._proxy_url() is None


class TestIsInsidePod:
    """Validate is_inside_pod detection."""

    def test_is_inside_pod_true(self) -> None:
        """Returns True when the serviceaccount secrets path exists."""
        with mock.patch("pathlib.Path.exists", return_value=True):
            assert k8sutils.is_inside_pod() is True

    def test_is_inside_pod_false(self) -> None:
        """Returns False when the serviceaccount secrets path is absent."""
        with mock.patch("pathlib.Path.exists", return_value=False):
            assert k8sutils.is_inside_pod() is False


class TestParseCpuQuantity:
    """Validate _parse_cpu_quantity unit conversions."""

    def test_empty_quantity(self) -> None:
        """Empty/None input returns 0.0."""
        assert k8sutils._parse_cpu_quantity("") == 0.0

    def test_millicores(self) -> None:
        """Millicore suffix is converted to cores."""
        assert k8sutils._parse_cpu_quantity("500m") == 0.5

    def test_plain_cores(self) -> None:
        """Values without a suffix are treated as whole cores."""
        assert k8sutils._parse_cpu_quantity("2") == 2.0


class TestParseMemoryQuantity:
    """Validate _parse_memory_quantity unit conversions."""

    def test_empty_quantity(self) -> None:
        """Empty/None input returns 0."""
        assert k8sutils._parse_memory_quantity("") == 0

    def test_kibibytes(self) -> None:
        """Ki suffix converts to bytes using binary multiples."""
        assert k8sutils._parse_memory_quantity("1Ki") == 1024

    def test_mebibytes(self) -> None:
        """Mi suffix converts to bytes using binary multiples."""
        assert k8sutils._parse_memory_quantity("1Mi") == 1024 * 1024

    def test_gibibytes(self) -> None:
        """Gi suffix converts to bytes using binary multiples."""
        assert k8sutils._parse_memory_quantity("1Gi") == 1024 * 1024 * 1024

    def test_kilobytes(self) -> None:
        """k suffix converts to bytes using decimal multiples."""
        assert k8sutils._parse_memory_quantity("1k") == 1000

    def test_megabytes(self) -> None:
        """M suffix converts to bytes using decimal multiples."""
        assert k8sutils._parse_memory_quantity("1M") == 1000000

    def test_gigabytes(self) -> None:
        """G suffix converts to bytes using decimal multiples."""
        assert k8sutils._parse_memory_quantity("1G") == 1000000000

    def test_plain_bytes(self) -> None:
        """Values without a suffix are treated as raw bytes."""
        assert k8sutils._parse_memory_quantity("512") == 512


def _make_container(cpu_req=None, mem_req=None, cpu_lim=None, mem_lim=None):
    requests = {}
    if cpu_req is not None:
        requests["cpu"] = cpu_req
    if mem_req is not None:
        requests["memory"] = mem_req
    limits = {}
    if cpu_lim is not None:
        limits["cpu"] = cpu_lim
    if mem_lim is not None:
        limits["memory"] = mem_lim
    return kubernetes.client.V1Container(
        name="app",
        resources=kubernetes.client.V1ResourceRequirements(
            requests=requests or None, limits=limits or None
        ),
    )


def _make_pod(containers):
    return kubernetes.client.V1Pod(spec=kubernetes.client.V1PodSpec(containers=containers))


class TestGetTotalPodMemory:
    """Validate get_total_pod_memory aggregation."""

    def test_sums_requests_and_limits_across_containers(self) -> None:
        """Memory requests/limits are summed and converted to MiB."""
        pod = _make_pod(
            [
                _make_container(mem_req="512Mi", mem_lim="1Gi"),
                _make_container(mem_req="256Mi", mem_lim="512Mi"),
            ]
        )
        request_mib, limit_mib = k8sutils.get_total_pod_memory(pod)
        assert request_mib == 768.0
        assert limit_mib == 1536.0

    def test_no_containers_returns_zero(self) -> None:
        """A pod with no containers reports zero memory usage."""
        pod = _make_pod([])
        assert k8sutils.get_total_pod_memory(pod) == (0.0, 0.0)

    def test_missing_resources_are_skipped(self) -> None:
        """Containers without memory requests/limits don't contribute."""
        pod = _make_pod([_make_container()])
        assert k8sutils.get_total_pod_memory(pod) == (0.0, 0.0)


class TestGetTotalPodCpu:
    """Validate get_total_pod_cpu aggregation."""

    def test_sums_requests_and_limits_across_containers(self) -> None:
        """CPU requests/limits are summed across containers."""
        pod = _make_pod(
            [
                _make_container(cpu_req="500m", cpu_lim="1"),
                _make_container(cpu_req="250m", cpu_lim="500m"),
            ]
        )
        request_cores, limit_cores = k8sutils.get_total_pod_cpu(pod)
        assert request_cores == 0.75
        assert limit_cores == 1.5

    def test_no_containers_returns_zero(self) -> None:
        """A pod with no containers reports zero CPU usage."""
        pod = _make_pod([])
        assert k8sutils.get_total_pod_cpu(pod) == (0.0, 0.0)


class TestGetPodContainerStatuses:
    """Validate get_pod_container_statuses state extraction."""

    def test_running_container(self) -> None:
        """A running container reports ready/started/state only."""
        pod = kubernetes.client.V1Pod(
            status=kubernetes.client.V1PodStatus(
                container_statuses=[
                    kubernetes.client.V1ContainerStatus(
                        name="app",
                        ready=True,
                        started=True,
                        image="img",
                        image_id="img-id",
                        restart_count=0,
                        state=kubernetes.client.V1ContainerState(
                            running=kubernetes.client.V1ContainerStateRunning()
                        ),
                    )
                ]
            )
        )
        statuses = k8sutils.get_pod_container_statuses(pod)
        assert statuses == {"app": {"ready": True, "started": True, "state": "Running"}}

    def test_terminated_container(self) -> None:
        """A terminated container includes exit code and reason."""
        pod = kubernetes.client.V1Pod(
            status=kubernetes.client.V1PodStatus(
                container_statuses=[
                    kubernetes.client.V1ContainerStatus(
                        name="app",
                        ready=False,
                        started=False,
                        image="img",
                        image_id="img-id",
                        restart_count=1,
                        state=kubernetes.client.V1ContainerState(
                            terminated=kubernetes.client.V1ContainerStateTerminated(
                                exit_code=1, reason="Error"
                            )
                        ),
                    )
                ]
            )
        )
        statuses = k8sutils.get_pod_container_statuses(pod)
        assert statuses["app"]["state"] == "Terminated"
        assert statuses["app"]["exit_code"] == 1
        assert statuses["app"]["reason"] == "Error"

    def test_waiting_container(self) -> None:
        """A waiting container includes the waiting reason."""
        pod = kubernetes.client.V1Pod(
            status=kubernetes.client.V1PodStatus(
                container_statuses=[
                    kubernetes.client.V1ContainerStatus(
                        name="app",
                        ready=False,
                        started=False,
                        image="img",
                        image_id="img-id",
                        restart_count=0,
                        state=kubernetes.client.V1ContainerState(
                            waiting=kubernetes.client.V1ContainerStateWaiting(
                                reason="ImagePullBackOff"
                            )
                        ),
                    )
                ]
            )
        )
        statuses = k8sutils.get_pod_container_statuses(pod)
        assert statuses["app"]["state"] == "Waiting"
        assert statuses["app"]["reason"] == "ImagePullBackOff"

    def test_no_container_statuses(self) -> None:
        """Pods without container statuses report an empty dict."""
        pod = kubernetes.client.V1Pod(status=kubernetes.client.V1PodStatus())
        assert k8sutils.get_pod_container_statuses(pod) == {}


class TestPod:
    """Validate the Pod dataclass constructed from API objects and dicts."""

    def _plain_pod_dict(self, **overrides) -> dict:
        data = {
            "metadata": {
                "uid": "uid-1",
                "name": "pod-1",
                "namespace": "default",
                "creation_timestamp": 1000,
                "deletion_timestamp": None,
                "resource_version": "42",
                "labels": {"app.kubernetes.io/name": "worker"},
            },
            "spec": {
                "node_name": "node-1",
                "containers": [
                    {
                        "image": "my-image",
                        "resources": {
                            "requests": {"cpu": "500m", "memory": "256Mi"},
                        },
                    }
                ],
            },
            "status": {
                "host_ip": "10.0.0.1",
                "pod_ip": "10.0.0.2",
                "phase": "Running",
                "start_time": 1000,
                "container_statuses": [{"restart_count": 2}],
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        }
        data.update(overrides)
        return data

    def test_pod_from_plain_dict(self) -> None:
        """Pod is correctly built from a plain dict payload."""
        pod = k8sutils.Pod(self._plain_pod_dict())
        assert pod.uid == "uid-1"
        assert pod.pod_name == "pod-1"
        assert pod.pod_type == "worker"
        assert pod.namespace == "default"
        assert pod.version == 42
        assert pod.node_name == "node-1"
        assert pod.node_ip == "10.0.0.1"
        assert pod.pod_ip == "10.0.0.2"
        assert pod.phase == "running"
        assert pod.start_time != ""
        assert pod.end_time == ""
        assert pod.image == "my-image"
        assert pod.cpu == 0.5
        assert round(pod.memory, 2) == 256.0
        assert pod.restart_count == 2
        assert pod.conditions == [{"type": "Ready", "status": "True"}]

    def test_pod_with_deletion_timestamp(self) -> None:
        """Deletion timestamp populates end_time."""
        data = self._plain_pod_dict()
        data["metadata"]["deletion_timestamp"] = 2000
        pod = k8sutils.Pod(data)
        assert pod.end_time != ""

    def test_pod_without_start_timestamp(self) -> None:
        """Missing start_time results in an empty start_time string."""
        data = self._plain_pod_dict()
        data["status"]["start_time"] = 0
        pod = k8sutils.Pod(data)
        assert pod.start_time == ""

    def test_pod_from_api_object(self) -> None:
        """Pod can be built from a real kubernetes API model object."""
        api_pod = kubernetes.client.V1Pod(
            metadata=kubernetes.client.V1ObjectMeta(
                uid="uid-2",
                name="pod-2",
                namespace="default",
                creation_timestamp=None,
                resource_version="7",
                labels={"app.kubernetes.io/name": "worker"},
            ),
            spec=kubernetes.client.V1PodSpec(
                node_name="node-2",
                containers=[_make_container(cpu_req="1", mem_req="1Gi")],
            ),
            status=kubernetes.client.V1PodStatus(phase="Pending"),
        )
        pod = k8sutils.Pod(api_pod)
        assert pod.uid == "uid-2"
        assert pod.pod_name == "pod-2"
        assert pod.phase == "pending"
        assert pod.cpu == 1.0


class TestNode:
    """Validate the Node dataclass constructed from API objects and dicts."""

    def _plain_node_dict(self, **overrides) -> dict:
        data = {
            "metadata": {
                "uid": "node-uid-1",
                "name": "node-1",
                "creation_timestamp": 1000,
                "deletion_timestamp": None,
                "resource_version": "9",
                "labels": {
                    "node.kubernetes.io/instance-type": "m5.large",
                    "karpenter.sh/capacity-type": "ON_DEMAND",
                    "topology.k8s.aws/zone-id": "use1-az1",
                },
            },
            "status": {
                "addresses": [{"type": "InternalIP", "address": "10.1.1.1"}],
                "capacity": {"cpu": "4", "memory": "16Gi"},
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        }
        data.update(overrides)
        return data

    def test_node_from_plain_dict(self) -> None:
        """Node is correctly built from a plain dict payload."""
        node = k8sutils.Node(self._plain_node_dict())
        assert node.uid == "node-uid-1"
        assert node.node_name == "node-1"
        assert node.instance_type == "m5.large"
        assert node.capacity_type == "on-demand"
        assert node.zone_id == "use1-az1"
        assert node.node_ip == "10.1.1.1"
        assert node.start_time != ""
        assert node.end_time == ""
        assert node.cpu == 4.0
        assert round(node.memory, 2) == 16384.0
        assert node.conditions == [{"type": "Ready", "status": "True"}]

    def test_node_capacity_type_fallback(self) -> None:
        """capacity-type falls back to the EKS label when karpenter is absent."""
        data = self._plain_node_dict()
        del data["metadata"]["labels"]["karpenter.sh/capacity-type"]
        data["metadata"]["labels"]["eks.amazonaws.com/capacityType"] = "SPOT"
        node = k8sutils.Node(data)
        assert node.capacity_type == "spot"

    def test_node_without_internal_ip(self) -> None:
        """Missing InternalIP address type leaves node_ip empty."""
        data = self._plain_node_dict()
        data["status"]["addresses"] = [{"type": "ExternalIP", "address": "1.2.3.4"}]
        node = k8sutils.Node(data)
        assert node.node_ip == ""

    def test_node_with_deletion_timestamp(self) -> None:
        """Deletion timestamp populates end_time."""
        data = self._plain_node_dict()
        data["metadata"]["deletion_timestamp"] = 2000
        node = k8sutils.Node(data)
        assert node.end_time != ""

    def test_node_memory_falls_back_to_allocatable(self) -> None:
        """When capacity is missing, allocatable is used instead."""
        data = self._plain_node_dict()
        del data["status"]["capacity"]
        data["status"]["allocatable"] = {"cpu": "2", "memory": "8Gi"}
        node = k8sutils.Node(data)
        assert node.cpu == 2.0


class TestGetNodeConditions:
    """Validate get_node_conditions extraction."""

    def test_extracts_conditions_by_type(self) -> None:
        """Conditions are keyed by their type with status/reason/message."""
        node = kubernetes.client.V1Node(
            status=kubernetes.client.V1NodeStatus(
                conditions=[
                    kubernetes.client.V1NodeCondition(
                        type="Ready",
                        status="True",
                        reason="KubeletReady",
                        message="kubelet is ready",
                    )
                ]
            )
        )
        conditions = k8sutils.get_node_conditions(node)
        assert conditions == {
            "Ready": {
                "status": "True",
                "reason": "KubeletReady",
                "message": "kubelet is ready",
            }
        }

    def test_no_conditions_returns_empty_dict(self) -> None:
        """A node without conditions reports an empty dict."""
        node = kubernetes.client.V1Node(status=kubernetes.client.V1NodeStatus())
        assert k8sutils.get_node_conditions(node) == {}


class TestGetNodeMemoryResources:
    """Validate get_node_memory_resources computation."""

    def test_computes_capacity_allocatable_and_reserved(self) -> None:
        """Reserved memory is capacity minus allocatable, converted to MiB."""
        node = kubernetes.client.V1Node(
            status=kubernetes.client.V1NodeStatus(
                capacity={"memory": "16Gi"},
                allocatable={"memory": "14Gi"},
            )
        )
        resources = k8sutils.get_node_memory_resources(node)
        assert resources["capacity"] == 16384.0
        assert resources["allocatable"] == 14336.0
        assert resources["reserved"] == 2048.0


class TestGetNodeCpuResources:
    """Validate get_node_cpu_resources computation."""

    def test_computes_capacity_allocatable_and_reserved(self) -> None:
        """Reserved CPU is capacity minus allocatable."""
        node = kubernetes.client.V1Node(
            status=kubernetes.client.V1NodeStatus(
                capacity={"cpu": "4"},
                allocatable={"cpu": "3.5"},
            )
        )
        resources = k8sutils.get_node_cpu_resources(node)
        assert resources["capacity"] == 4.0
        assert resources["allocatable"] == 3.5
        assert resources["reserved"] == 0.5


class TestParseNodeEvent:
    """Validate parse_node_event filtering and extraction."""

    def _event(self, kind="Node", name="node-1", uid="uid-1"):
        return kubernetes.client.CoreV1Event(
            involved_object=kubernetes.client.V1ObjectReference(kind=kind, name=name, uid=uid),
            metadata=kubernetes.client.V1ObjectMeta(
                name="evt-1", creation_timestamp=mock.Mock(timestamp=lambda: 123.0)
            ),
            type="Warning",
            reason="NodeNotReady",
            message="node not ready",
            reporting_component="kubelet",
        )

    def test_skips_event_with_missing_object_uid(self) -> None:
        """Events whose object name equals its uid are considered incomplete."""
        event = self._event(name="same", uid="same")
        assert k8sutils.parse_node_event(event) is None

    def test_skips_non_node_events(self) -> None:
        """Events not involving a Node object are skipped."""
        event = self._event(kind="Pod")
        assert k8sutils.parse_node_event(event) is None

    def test_parses_valid_node_event(self) -> None:
        """A valid node event is converted into the expected dict shape."""
        event = self._event()
        parsed = k8sutils.parse_node_event(event)
        assert parsed == {
            "type": "Warning",
            "reason": "NodeNotReady",
            "message": "node not ready",
            "source": "kubelet",
            "timestamp": 123,
        }


class TestGetKubernetesClient:
    """Validate get_kubernetes_client caching behaviour."""

    def test_returns_cached_core_v1_api_instance(self) -> None:
        """Repeated calls return the exact same cached CoreV1Api instance."""
        k8sutils.get_kubernetes_client.cache_clear()
        try:
            client_a = k8sutils.get_kubernetes_client()
            client_b = k8sutils.get_kubernetes_client()
            assert client_a is client_b
            assert isinstance(client_a, kubernetes.client.CoreV1Api)
        finally:
            k8sutils.get_kubernetes_client.cache_clear()


class TestLoadK8sConfig:
    """Validate load_k8s_config credential loading paths."""

    def test_loads_incluster_config_when_inside_pod(self, monkeypatch) -> None:
        """Inside a pod, incluster config is loaded and no proxy is set."""
        monkeypatch.setattr(k8sutils, "is_inside_pod", lambda: True)
        with (
            mock.patch("kubernetes.config.load_incluster_config") as incluster,
            mock.patch("kubernetes.config.load_kube_config") as kube_config,
        ):
            k8sutils.load_k8s_config()
        incluster.assert_called_once()
        kube_config.assert_not_called()

    def test_loads_local_config_and_sets_proxy(self, monkeypatch) -> None:
        """Outside a pod, local kubeconfig is loaded and proxy is configured."""
        monkeypatch.setattr(k8sutils, "is_inside_pod", lambda: False)
        monkeypatch.setattr(
            kubernetes.client.Configuration, "_default", kubernetes.client.Configuration()
        )
        with mock.patch("kubernetes.config.load_kube_config") as kube_config:
            k8sutils.load_k8s_config(proxy_url="http://proxy:8080")
        kube_config.assert_called_once()
        assert (
            kubernetes.client.Configuration._default.proxy == "http://proxy:8080"
        )

    def test_loads_local_config_without_proxy(self, monkeypatch) -> None:
        """When no proxy is provided or configured, none is set on the client."""
        monkeypatch.setattr(k8sutils, "is_inside_pod", lambda: False)
        monkeypatch.setattr(k8sutils, "_proxy_url", lambda: None)
        with mock.patch("kubernetes.config.load_kube_config") as kube_config:
            k8sutils.load_k8s_config()
        kube_config.assert_called_once()

    def test_retries_on_api_exception_then_succeeds(self, monkeypatch) -> None:
        """Transient ApiExceptions are retried without raising, until success."""
        monkeypatch.setattr(k8sutils, "is_inside_pod", lambda: False)
        k8sutils.load_k8s_config.retry.sleep = lambda _seconds: None
        calls = {"count": 0}

        def flaky_load(*_args, **_kwargs):
            calls["count"] += 1
            if calls["count"] < 2:
                raise kubernetes.client.exceptions.ApiException(status=500)

        with mock.patch("kubernetes.config.load_kube_config", side_effect=flaky_load):
            k8sutils.load_k8s_config()
        assert calls["count"] == 2


class TestGetNamespace:
    """Validate get_namespace resolution paths."""

    def test_namespace_inside_pod_reads_serviceaccount_file(self, monkeypatch, tmp_path) -> None:
        """Inside a pod, the namespace is read from the serviceaccount file."""
        monkeypatch.setattr(k8sutils, "is_inside_pod", lambda: True)
        ns_file = tmp_path / "namespace"
        ns_file.write_text("my-namespace\n")
        with mock.patch("pathlib.Path.read_text", return_value="my-namespace\n"):
            assert k8sutils.get_namespace() == "my-namespace"

    def test_namespace_outside_pod_reads_kube_config_contexts(self, monkeypatch) -> None:
        """Outside a pod, the namespace comes from the active kubeconfig context."""
        monkeypatch.setattr(k8sutils, "is_inside_pod", lambda: False)
        contexts = (
            [],
            {"context": {"namespace": "local-namespace"}},
        )
        with mock.patch("kubernetes.config.list_kube_config_contexts", return_value=contexts):
            assert k8sutils.get_namespace() == "local-namespace"


class TestListNode:
    """Validate list_node delegates to the cached client."""

    def test_returns_items_from_client(self, monkeypatch) -> None:
        """list_node returns the .items of the client's list_node() response."""
        mock_client = mock.MagicMock()
        mock_client.list_node.return_value = mock.Mock(items=["node-a", "node-b"])
        monkeypatch.setattr(k8sutils, "get_kubernetes_client", lambda: mock_client)
        assert k8sutils.list_node() == ["node-a", "node-b"]


class TestWatchEvents:
    """Validate _watch_events single-cycle watch stream handling."""

    def test_handles_each_event_and_warns_on_stream_end(self, monkeypatch) -> None:
        """Each event from the stream is dispatched to the handler."""
        events = [{"type": "ADDED"}, {"type": "MODIFIED"}]
        handler = mock.Mock()
        with mock.patch("kubernetes.watch.Watch.stream", return_value=iter(events)):
            result = k8sutils._watch_events(
                mock.Mock(),
                handler,
                "workdir",
                "postprocess",
                "event_path",
                "0",
            )
        assert handler.call_count == 2
        assert result == "0"

    def test_resource_version_mismatch_restarts_from_new_version(self) -> None:
        """A GONE ApiException extracts the resourceVersion to resume from."""
        obj = mock.Mock()
        obj.metadata.resource_version = "99"
        event = {"object": obj}

        def _stream(*_args, **_kwargs):
            yield event
            raise kubernetes.client.exceptions.ApiException(status=HTTPStatus.GONE)

        handler = mock.Mock()
        with mock.patch("kubernetes.watch.Watch.stream", side_effect=_stream):
            result = k8sutils._watch_events(
                mock.Mock(), handler, "workdir", "postprocess", "event_path", "0"
            )
        assert result == "99"

    def test_protocol_error_soft_restarts(self) -> None:
        """A ProtocolError during streaming triggers a soft restart."""

        def _stream(*_args, **_kwargs):
            # Unreachable yield forces generator semantics.
            if False:
                yield
            raise urllib3.exceptions.ProtocolError("broken pipe")

        with mock.patch("kubernetes.watch.Watch.stream", side_effect=_stream):
            result = k8sutils._watch_events(
                mock.Mock(), mock.Mock(), "workdir", "postprocess", "event_path", "5"
            )
        assert result == "5"

    def test_unexpected_exception_propagates(self) -> None:
        """Non-retryable exceptions from the stream propagate immediately."""

        def _stream(*_args, **_kwargs):
            raise ValueError("boom")

        with (
            mock.patch("kubernetes.watch.Watch.stream", side_effect=_stream),
            pytest.raises(ValueError, match="boom"),
        ):
            k8sutils._watch_events(
                mock.Mock(), mock.Mock(), "workdir", "postprocess", "event_path", "0"
            )
