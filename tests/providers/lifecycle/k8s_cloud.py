"""Kubernetes ground truth: kmock holds the objects, this layer steers and records.

kmock is an apiserver without controllers. This layer adds the two controllers
the scenarios need, as a small state machine that only moves when ORB calls the
API or the scenario steps it:

* kubelet: a created pod is Running with an address unless the scenario holds it
  Pending;
* ReplicaSet: a Deployment gets pods up to ``spec.replicas`` unless the scenario
  blocks creation (quota), and scale-down removes the pods with the lowest
  ``controller.kubernetes.io/pod-deletion-cost`` first, then by name.

Every mutating API call is logged with the pods it killed, expanded from the
objects as they were when the call arrived.
"""

from __future__ import annotations

import itertools
from typing import Any, Optional
from urllib.parse import urlparse

from tests.providers.lifecycle.cloud import (
    PENDING,
    RUNNING,
    STOPPING,
    TERMINATED,
    CallRecord,
    Cloud,
    CloudMachine,
)

REQUEST_LABEL = "orb.io/request-id"
DELETION_COST = "controller.kubernetes.io/pod-deletion-cost"


def _plain(value: Any) -> Any:
    if hasattr(value, "items"):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


class K8sCloud(Cloud):
    def __init__(self, emulator: Any, namespace: str = "orb-test") -> None:
        from kmock import resource

        self.kmock = emulator
        self.namespace = namespace
        self.call_log: list[CallRecord] = []
        self.pods = resource("", "v1", "pods")
        self.deployments = resource("apps", "v1", "deployments")
        self._hold_budget = 0
        self._lag = False
        self._blocked = False
        self._capacity: Optional[int] = None
        self._fail_abort = False
        self._fail_reads = False
        self._addresses = itertools.count(10)
        self._stopping: set[str] = set()
        self._gone: set[str] = set()
        self._owners: dict[str, Optional[str]] = {}
        self._pending: set[str] = set()
        self._counter = itertools.count(1)

    # ---- installation -------------------------------------------------------------

    def install(self, api_client: Any) -> None:
        original = api_client.call_api
        cloud = self

        def call_api(method: str, url: str, *args: Any, **kwargs: Any) -> Any:
            verb = method.upper()
            if verb == "GET":
                if cloud._fail_reads:
                    from kubernetes.client.rest import ApiException

                    raise ApiException(status=500, reason="Internal Server Error")
                return original(method, url, *args, **kwargs)
            body = kwargs.get("body")
            if body is None and len(args) > 1:
                body = args[1]
            return cloud._mutate(
                original, verb, urlparse(url).path, body, method, url, args, kwargs
            )

        api_client.call_api = call_api

    # ---- mutation hooks -----------------------------------------------------------

    def _mutate(
        self,
        original: Any,
        verb: str,
        path: str,
        body: Any,
        method: str,
        url: str,
        args: tuple,
        kwargs: dict,
    ) -> Any:
        kind, name, sub = self._parse(path)
        record = CallRecord(
            seq=len(self.call_log),
            operation=f"{verb} {kind}{'/' + sub if sub else ''}",
            params={"path": path, "body": _plain(body) if body is not None else None},
            resource=name,
        )
        self.call_log.append(record)
        if verb == "DELETE" and kind == "pods" and name:
            before = self._pod(name)
            record.killed = (
                {name} if before and self._state(before) in (PENDING, RUNNING) else set()
            )
            result = original(method, url, *args, **kwargs)
            self._after_pod_delete(name, before)
            return result
        if verb == "DELETE" and kind == "deployments" and name:
            if self._fail_abort:
                self._fail_abort = False
                from kubernetes.client.rest import ApiException

                raise ApiException(status=403, reason="Forbidden")
            members = self._deployment_pods(name)
            record.operation = "DeleteDeployment"
            record.killed = {n for n, p in members if self._state(p) in (PENDING, RUNNING)}
            result = original(method, url, *args, **kwargs)
            for pod_name, pod in members:
                self._after_pod_delete(pod_name, pod)
            return result
        result = original(method, url, *args, **kwargs)
        if verb == "POST" and kind == "pods":
            self._after_pod_create(body)
        elif verb == "POST" and kind == "deployments":
            self._after_deployment_create(body)
        elif verb == "PATCH" and kind == "deployments" and name:
            replicas = (_plain(body) or {}).get("spec", {}).get("replicas")
            if replicas is not None:
                self._scale(name, int(replicas), record)
        return result

    @staticmethod
    def _parse(path: str) -> tuple[str, Optional[str], Optional[str]]:
        parts = [p for p in path.split("/") if p]
        if "namespaces" in parts:
            tail = parts[parts.index("namespaces") + 2 :]
        else:
            tail = parts[-1:]
        kind = tail[0] if tail else ""
        name = tail[1] if len(tail) > 1 else None
        sub = tail[2] if len(tail) > 2 else None
        return kind, name, sub

    # ---- object helpers -----------------------------------------------------------

    def _pod(self, name: str) -> Optional[dict[str, Any]]:
        obj = self.kmock.objects[self.pods, self.namespace, name]
        return None if obj.deleted else _plain(obj.last)

    def _all_pods(self) -> list[tuple[str, dict[str, Any]]]:
        out = []
        for key in list(self.kmock.objects):
            if key[0] != self.pods:
                continue
            obj = self.kmock.objects[key]
            if not obj.deleted:
                out.append((key[2], _plain(obj.last)))
        return out

    def _deployment(self, name: str) -> Optional[dict[str, Any]]:
        obj = self.kmock.objects[self.deployments, self.namespace, name]
        return None if obj.deleted else _plain(obj.last)

    def _deployment_pods(self, name: str) -> list[tuple[str, dict[str, Any]]]:
        return [
            (n, p)
            for n, p in self._all_pods()
            if (p.get("metadata", {}).get("labels", {}) or {}).get("orb.io/deployment") == name
            or n.startswith(f"{name}-")
        ]

    @staticmethod
    def _state(pod: dict[str, Any]) -> str:
        if pod.get("metadata", {}).get("deletionTimestamp"):
            return STOPPING
        phase = (pod.get("status") or {}).get("phase")
        return RUNNING if phase == "Running" else PENDING

    def _set_status(self, name: str, running: bool) -> None:
        obj = self.kmock.objects[self.pods, self.namespace, name]
        if running:
            n = next(self._addresses)
            obj["status"] = {
                "phase": "Running",
                "podIP": f"10.244.0.{n}",
                "hostIP": "192.168.0.10",
                "startTime": "2026-01-01T00:00:00Z",
                "conditions": [{"type": "Ready", "status": "True"}],
                "containerStatuses": [],
            }
        else:
            obj["status"] = {
                "phase": "Pending",
                "conditions": [{"type": "PodScheduled", "status": "True"}],
                "containerStatuses": [],
            }
            self._pending.add(name)

    # ---- kubelet / ReplicaSet -----------------------------------------------------

    def _launch(self, name: str) -> None:
        if self._hold_budget > 0:
            self._hold_budget -= 1
            self._set_status(name, running=False)
        else:
            self._set_status(name, running=True)

    def _after_pod_create(self, body: Any) -> None:
        name = (_plain(body) or {}).get("metadata", {}).get("name")
        if not name:
            return
        if self._capacity is not None:
            if self._capacity <= 0:
                self._set_status(name, running=False)
                self._owners[name] = self._owner_of(name)
                return
            self._capacity -= 1
        self._launch(name)
        self._owners[name] = self._owner_of(name)

    def _owner_of(self, name: str) -> Optional[str]:
        pod = self._pod(name)
        return ((pod or {}).get("metadata", {}).get("labels", {}) or {}).get(REQUEST_LABEL)

    def _after_deployment_create(self, body: Any) -> None:
        spec = (_plain(body) or {}).get("spec", {})
        name = (_plain(body) or {}).get("metadata", {}).get("name")
        self._reconcile(name, int(spec.get("replicas", 1)))

    def _reconcile(self, deployment: str, replicas: int) -> None:
        if self._blocked:
            return
        current = [n for n, p in self._deployment_pods(deployment) if self._state(p) != STOPPING]
        dep = self._deployment(deployment) or {}
        template = dep.get("spec", {}).get("template") or {}
        labels = dict((template.get("metadata", {}) or {}).get("labels", {}) or {})
        for _ in range(max(0, replicas - len(current))):
            if self._capacity is not None:
                if self._capacity <= 0:
                    break
                self._capacity -= 1
            name = f"{deployment}-{next(self._counter):05d}"
            self.kmock.objects[self.pods, self.namespace, name] = {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": name,
                    "namespace": self.namespace,
                    "labels": labels,
                    "annotations": {},
                },
                "spec": {
                    "containers": template.get("spec", {}).get("containers")
                    or [{"name": "app", "image": "busybox:latest"}]
                },
            }
            self._launch(name)
            self._owners[name] = labels.get(REQUEST_LABEL)

    def _scale(self, deployment: str, replicas: int, record: CallRecord) -> None:
        dep = self.kmock.objects[self.deployments, self.namespace, deployment]
        if not dep.deleted:
            spec = _plain(dep.last).get("spec", {})
            spec["replicas"] = replicas
            dep["spec"] = spec
        members = [
            (n, p) for n, p in self._deployment_pods(deployment) if self._state(p) != STOPPING
        ]
        surplus = len(members) - replicas
        if surplus <= 0:
            self._reconcile(deployment, replicas)
            return

        def cost(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
            annotations = item[1].get("metadata", {}).get("annotations", {}) or {}
            return int(annotations.get(DELETION_COST, 0)), item[0]

        victims = sorted(members, key=cost)[:surplus]
        for pod_name, pod in victims:
            record.killed.add(pod_name)
            obj = self.kmock.objects[self.pods, self.namespace, pod_name]
            obj.delete()
            self._after_pod_delete(pod_name, pod)

    def _after_pod_delete(self, name: str, before: Optional[dict[str, Any]]) -> None:
        """A deleted pod either goes away at once or lingers as Terminating."""
        if before is None:
            return
        if self._lag:
            restored = dict(before)
            meta = dict(restored.get("metadata", {}))
            meta["deletionTimestamp"] = "2026-01-01T00:00:00Z"
            restored["metadata"] = meta
            obj = self.kmock.objects[self.pods, self.namespace, name]
            for key, value in restored.items():
                obj[key] = value
            self._stopping.add(name)
        else:
            self._gone.add(name)

    # ---- Cloud API ----------------------------------------------------------------

    def machines(self, request_id: Optional[str] = None) -> list[CloudMachine]:
        out = []
        live = dict(self._all_pods())
        for name in sorted(set(live) | self._gone | set(self._owners)):
            pod = live.get(name)
            owner = self._owners.get(name)
            if pod is not None:
                owner = (pod.get("metadata", {}).get("labels", {}) or {}).get(REQUEST_LABEL, owner)
                state = self._state(pod)
                ip = (pod.get("status") or {}).get("podIP")
                image = (pod.get("spec", {}).get("containers") or [{}])[0].get("image")
            else:
                state, ip, image = TERMINATED, None, None
            if request_id is not None and owner != request_id:
                continue
            out.append(CloudMachine(name, state, owner, ip, image))
        return out

    def advance(self) -> None:
        self._hold_budget = 0
        self._lag = False
        self._blocked = False
        for name in sorted(self._pending):
            if self._pod(name) is not None:
                self._set_status(name, running=True)
        self._pending.clear()
        for name in sorted(self._stopping):
            obj = self.kmock.objects[self.pods, self.namespace, name]
            if not obj.deleted:
                obj.delete()
            self._gone.add(name)
        self._stopping.clear()
        for key in list(self.kmock.objects):
            if key[0] == self.deployments:
                dep = self._deployment(key[2])
                if dep is not None:
                    self._reconcile(key[2], int(dep.get("spec", {}).get("replicas", 1)))

    def hold_pending(self, count: Optional[int] = None) -> None:
        self._hold_budget = 10**6 if count is None else count

    def release_pending(self) -> None:
        self._hold_budget = 0
        for name in sorted(self._pending):
            if self._pod(name) is not None:
                self._set_status(name, running=True)
        self._pending.clear()

    def lag_termination(self) -> None:
        self._lag = True

    def limit_capacity(self, machines: int) -> None:
        self._capacity = machines

    def block_pod_creation(self) -> None:
        """Quota exceeded: the Deployment exists, its ReplicaSet creates no pods."""
        self._blocked = True

    def interrupt(self, machine_ids: list[str]) -> None:
        for name in machine_ids:
            obj = self.kmock.objects[self.pods, self.namespace, name]
            if not obj.deleted:
                obj.delete()
            self._gone.add(name)

    def fail_next_abort(self) -> None:
        self._fail_abort = True

    def fail_reads(self, failing: bool = True) -> None:
        self._fail_reads = failing

    def unknown_machine_id(self) -> str:
        return "orb-unknown-pod-00000"

    def replicas(self, request_id: str) -> int:
        """Target capacity of the request's Deployment (spec.replicas)."""
        for key in list(self.kmock.objects):
            if key[0] == self.deployments:
                dep = self._deployment(key[2])
                labels = (
                    (dep or {}).get("spec", {}).get("template", {}).get("metadata", {}) or {}
                ).get("labels", {}) or {}
                if dep and labels.get(REQUEST_LABEL) == request_id:
                    return int(dep["spec"].get("replicas", 0))
        raise LookupError(f"no Deployment for {request_id}")
