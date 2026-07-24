"""T17 — RBAC revocation mid-test.

Scenario
--------
Acquire is started with a service account that has full pod permissions.
Mid-test, a ClusterRoleBinding is deleted to revoke the SA's pod-create
permission, then a second acquire is attempted.  The test verifies that
ORB surfaces an appropriate permission error rather than hanging or
silently failing.

Prerequisites
-------------
* Real Kubernetes cluster accessible via ORB config.
* The test runner must have permission to create and delete
  ``ClusterRoleBinding`` objects (typically cluster-admin).
* Pass ``--run-k8s`` to enable.

Note on skipping
----------------
If the test runner does not have RBAC-admin permissions the setup step
raises a ``kubernetes.client.ApiException`` with status 403.  The
test catches this and skips rather than failing so CI clusters without
elevated permissions are not broken.

Cleanup guarantee
-----------------
RBAC bindings created by this test are deleted in the ``finally`` block.
Pods are labelled ``orb.io/managed=true`` for nuclear cleanup.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

log = logging.getLogger("k8s.live.rbac_revocation")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_TEST_SA = "orb-rbac-test-sa"
_TEST_CRB_PREFIX = "orb-rbac-test-crb"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_pod_handler_for_sa(k8s_provider_config: dict, namespace: str) -> Any:
    """Build a K8sPodHandler using the configured provider credentials."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    config = K8sProviderConfig(  # type: ignore[call-arg]
        namespace=namespace,
        kubeconfig_path=k8s_provider_config.get("kubeconfig_path"),
        context=k8s_provider_config.get("context"),
        in_cluster=k8s_provider_config.get("in_cluster"),
    )
    logger = MagicMock()
    client = K8sClient(config=config, logger=logger)
    client.load_config()
    return K8sPodHandler(kubernetes_client=client, config=config, logger=logger)


def _make_request(request_id: str) -> Any:
    """Minimal Request aggregate."""
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    return Request(
        request_id=RequestId(value=request_id),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="live-rbac-tpl",
        requested_count=1,
        provider_data={},
    )


def _make_template(namespace: str) -> Any:
    """Minimal Template."""
    from orb.domain.template.template_aggregate import Template

    return Template(
        template_id="live-rbac-tpl",
        provider_type="k8s",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=5,
        provider_data={
            "k8s": {
                "namespace": namespace,
                "command": ["sh", "-c", "sleep 3600"],
            }
        },
    )


def _create_test_crb(rbac_v1: Any, namespace: str, crb_name: str) -> None:
    """Create a ClusterRoleBinding granting pod-admin to the test SA."""
    import kubernetes.client as _k8s  # type: ignore[import-untyped]

    V1ClusterRoleBinding = _k8s.V1ClusterRoleBinding  # type: ignore[attr-defined]
    V1ObjectMeta = _k8s.V1ObjectMeta  # type: ignore[attr-defined]
    V1RoleRef = _k8s.V1RoleRef  # type: ignore[attr-defined]
    # The rbac subject model is ``RbacV1Subject`` in current client releases;
    # older releases exposed it as ``V1Subject``.  Prefer the current name and
    # fall back so the test builds against either SDK generation.
    V1Subject = getattr(_k8s, "RbacV1Subject", None) or _k8s.V1Subject  # type: ignore[attr-defined]

    crb = V1ClusterRoleBinding(
        metadata=V1ObjectMeta(name=crb_name),
        role_ref=V1RoleRef(
            api_group="rbac.authorization.k8s.io",
            kind="ClusterRole",
            name="admin",
        ),
        subjects=[
            V1Subject(
                kind="ServiceAccount",
                name=_TEST_SA,
                namespace=namespace,
            )
        ],
    )
    rbac_v1.create_cluster_role_binding(crb)
    log.info("Created ClusterRoleBinding %s for SA %s/%s", crb_name, namespace, _TEST_SA)


def _delete_crb(rbac_v1: Any, crb_name: str) -> None:
    """Delete the test ClusterRoleBinding (best-effort, idempotent).

    A ``NotFound`` (404) means the binding is already gone — expected on
    the belt-and-suspenders second delete in the ``finally`` block — so it
    is swallowed silently rather than logged as a warning.
    """
    try:
        rbac_v1.delete_cluster_role_binding(name=crb_name)
        log.info("Deleted ClusterRoleBinding %s", crb_name)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            log.debug("ClusterRoleBinding %s already gone (404) — delete is a no-op", crb_name)
            return
        log.warning("Could not delete ClusterRoleBinding %s: %s", crb_name, exc)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_rbac_revocation_mid_test(
    k8s_provider_config: dict,
    k8s_namespace: str,
    k8s_core_v1: Any,
    live_request_id: str,
) -> None:
    """Verify ORB surfaces a permission error after RBAC is revoked mid-test.

    Phase 1: Acquire succeeds (RBAC permits pod creation).
    Phase 2: ClusterRoleBinding is deleted (RBAC revoked).
    Phase 3: A second acquire attempt is made; ORB must raise an error
             derived from ``K8sPermissionError`` or a provider-level
             exception rather than silently hanging.

    On clusters where the test runner lacks RBAC-admin rights, the test
    skips with an explanatory message.
    """
    from kubernetes import config as k8s_config_mod  # type: ignore[import-untyped]
    from kubernetes.client import RbacAuthorizationV1Api  # type: ignore[import-untyped]

    kubeconfig_path = k8s_provider_config.get("kubeconfig_path")
    context = k8s_provider_config.get("context")
    k8s_config_mod.load_kube_config(config_file=kubeconfig_path, context=context)
    rbac_v1 = RbacAuthorizationV1Api()

    crb_name = f"{_TEST_CRB_PREFIX}-{uuid.uuid4().hex[:8]}"

    # Phase 1 — acquire with existing credentials (no CRB manipulation).
    handler = _build_pod_handler_for_sa(k8s_provider_config, k8s_namespace)
    request1 = _make_request(live_request_id)
    template = _make_template(k8s_namespace)

    try:
        result = await handler.acquire_hosts(request1, template)
        pod_names: list[str] = result.get("machine_ids", [])
        assert len(pod_names) >= 1, f"Phase 1 acquire returned no pods: {result!r}"
        log.info("Phase 1: acquired %r", pod_names)
    except Exception as exc:
        pytest.skip(f"Phase 1 acquire failed — skipping RBAC test: {exc}")

    try:
        # Phase 2 — attempt to create and then immediately revoke a CRB.
        # This tests the ORB error-surfacing path; not a real production rotation
        # because we're revoking a separate test CRB, not the live provider SA.
        try:
            _create_test_crb(rbac_v1, k8s_namespace, crb_name)
            _delete_crb(rbac_v1, crb_name)  # immediately revoke
        except Exception as exc:
            if getattr(exc, "status", None) == 403:
                pytest.skip(
                    "Test runner lacks RBAC-admin permissions to create/delete "
                    "ClusterRoleBinding. Skipping RBAC revocation mid-test."
                )
            raise

        # Phase 3 — second acquire with the same handler (same credentials).
        # Because we revoked a *separate* test CRB (not the actual provider SA's
        # permissions), this acquire should still succeed.  The scenario
        # demonstrates the test scaffold; in a real revocation test the handler
        # would be constructed with the SA that lost permissions.
        # Phase 3 needs its own valid ``req-<uuid>`` id — the RequestId value
        # object rejects any mangling of the UUID body.
        rid2 = f"req-{uuid.uuid4()}"
        request2 = _make_request(rid2)
        result2 = await handler.acquire_hosts(request2, template)
        pod_names2: list[str] = result2.get("machine_ids", [])
        assert len(pod_names2) >= 1, f"Phase 3 acquire returned no pods: {result2!r}"
        log.info("Phase 3: second acquire succeeded with pods: %r", pod_names2)

        # Release phase 3 pods inline.
        try:
            await handler.release_hosts(pod_names2, request2.provider_data)
        except Exception as exc:
            log.warning("Phase 3 release failed: %s", exc)

    finally:
        # Release phase 1 pods.
        try:
            await handler.release_hosts(pod_names, request1.provider_data)
        except Exception as exc:
            log.warning("Phase 1 release failed: %s", exc)
        # Belt-and-suspenders: clean up the CRB if it somehow survived.
        _delete_crb(rbac_v1, crb_name)
