"""Unit tests for :mod:`orb.providers.k8s.utilities.pod_spec_audit`.

Covers the full catalogue of high-risk field detections: hostNetwork,
privileged containers, hostPath volumes, SYS_ADMIN capability, and the
clean-spec (no-warning) path.  Also covers the reject-mode path that
raises :class:`K8sError` when findings are present.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.exceptions.k8s_exceptions import K8sError
from orb.providers.k8s.utilities.pod_spec_audit import audit_pod_spec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> MagicMock:
    """Return a mock logger with a ``warning`` method."""
    logger = MagicMock()
    logger.warning = MagicMock()
    return logger


def _bare_spec(**kwargs: object) -> dict:
    """Build a minimal bare spec dict (containers required by K8s schema)."""
    return {
        "containers": [{"name": "orb", "image": "busybox:latest"}],
        **kwargs,
    }


# ---------------------------------------------------------------------------
# No high-risk fields → no warnings
# ---------------------------------------------------------------------------


def test_clean_spec_produces_no_warnings() -> None:
    spec = _bare_spec()
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert findings == []
    logger.warning.assert_not_called()


def test_empty_dict_produces_no_warnings() -> None:
    logger = _make_logger()
    findings = audit_pod_spec({}, logger)
    assert findings == []


def test_non_dict_input_is_ignored() -> None:
    logger = _make_logger()
    findings = audit_pod_spec(None, logger)  # type: ignore[arg-type]
    assert findings == []


# ---------------------------------------------------------------------------
# hostNetwork
# ---------------------------------------------------------------------------


def test_host_network_true_triggers_warning() -> None:
    spec = _bare_spec(hostNetwork=True)
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert len(findings) == 1
    assert "hostNetwork" in findings[0]
    logger.warning.assert_called_once()


def test_host_network_false_no_warning() -> None:
    spec = _bare_spec(hostNetwork=False)
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert findings == []


def test_host_network_snake_case_triggers_warning() -> None:
    """SDK to_dict() uses snake_case — must still trigger a warning."""
    spec = _bare_spec(host_network=True)
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert len(findings) == 1
    assert "hostNetwork" in findings[0]


# ---------------------------------------------------------------------------
# hostPID / hostIPC
# ---------------------------------------------------------------------------


def test_host_pid_true_triggers_warning() -> None:
    spec = _bare_spec(hostPID=True)
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert any("hostPID" in f for f in findings)


def test_host_ipc_true_triggers_warning() -> None:
    spec = _bare_spec(hostIPC=True)
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert any("hostIPC" in f for f in findings)


# ---------------------------------------------------------------------------
# hostPath volumes
# ---------------------------------------------------------------------------


def test_host_path_volume_triggers_warning() -> None:
    spec = _bare_spec(
        volumes=[
            {"name": "data", "hostPath": {"path": "/var/data"}},
        ]
    )
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert len(findings) == 1
    assert "hostPath" in findings[0]
    assert "/var/data" in findings[0]
    logger.warning.assert_called_once()


def test_empty_volume_list_no_warning() -> None:
    spec = _bare_spec(volumes=[])
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert findings == []


def test_non_host_path_volume_no_warning() -> None:
    spec = _bare_spec(
        volumes=[
            {"name": "config", "configMap": {"name": "my-config"}},
        ]
    )
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert findings == []


def test_host_path_snake_case_triggers_warning() -> None:
    spec = _bare_spec(
        volumes=[
            {"name": "host-vol", "host_path": {"path": "/etc"}},
        ]
    )
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert len(findings) == 1
    assert "hostPath" in findings[0]


# ---------------------------------------------------------------------------
# Privileged containers
# ---------------------------------------------------------------------------


def test_privileged_container_triggers_warning() -> None:
    spec = {
        "containers": [
            {
                "name": "worker",
                "image": "busybox:latest",
                "securityContext": {"privileged": True},
            }
        ]
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert len(findings) == 1
    assert "privileged" in findings[0]
    assert "worker" in findings[0]


def test_non_privileged_container_no_warning() -> None:
    spec = {
        "containers": [
            {
                "name": "worker",
                "image": "busybox:latest",
                "securityContext": {"privileged": False},
            }
        ]
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert findings == []


# ---------------------------------------------------------------------------
# allowPrivilegeEscalation
# ---------------------------------------------------------------------------


def test_allow_privilege_escalation_triggers_warning() -> None:
    spec = {
        "containers": [
            {
                "name": "app",
                "image": "alpine",
                "securityContext": {"allowPrivilegeEscalation": True},
            }
        ]
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert any("allowPrivilegeEscalation" in f for f in findings)


def test_allow_privilege_escalation_snake_case_triggers_warning() -> None:
    spec = {
        "containers": [
            {
                "name": "app",
                "image": "alpine",
                "security_context": {"allow_privilege_escalation": True},
            }
        ]
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert any("allowPrivilegeEscalation" in f for f in findings)


# ---------------------------------------------------------------------------
# runAsUser == 0
# ---------------------------------------------------------------------------


def test_run_as_user_zero_triggers_warning() -> None:
    spec = {
        "containers": [
            {
                "name": "root-app",
                "image": "alpine",
                "securityContext": {"runAsUser": 0},
            }
        ]
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert any("runAsUser" in f for f in findings)


def test_run_as_user_non_zero_no_warning() -> None:
    spec = {
        "containers": [
            {
                "name": "safe-app",
                "image": "alpine",
                "securityContext": {"runAsUser": 1000},
            }
        ]
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert findings == []


# ---------------------------------------------------------------------------
# Pod-level securityContext.runAsUser == 0
# ---------------------------------------------------------------------------


def test_pod_level_run_as_user_zero_triggers_warning() -> None:
    """A pod-level runAsUser=0 with no container override must be flagged."""
    spec = {
        "securityContext": {"runAsUser": 0},
        "containers": [{"name": "app", "image": "alpine"}],
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert len(findings) == 1
    assert "securityContext.runAsUser" in findings[0]
    assert "app" in findings[0]
    logger.warning.assert_called_once()


def test_pod_level_run_as_user_zero_snake_case_triggers_warning() -> None:
    spec = {
        "security_context": {"run_as_user": 0},
        "containers": [{"name": "app", "image": "alpine"}],
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert any("securityContext.runAsUser" in f for f in findings)


def test_pod_level_run_as_user_nonzero_no_warning() -> None:
    spec = {
        "securityContext": {"runAsUser": 1000},
        "containers": [{"name": "app", "image": "alpine"}],
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert findings == []


def test_container_overrides_pod_level_run_as_user() -> None:
    """A container-level runAsUser overrides the pod-level setting for that
    container, so no finding should be reported for it."""
    spec = {
        "securityContext": {"runAsUser": 0},
        "containers": [
            {
                "name": "app",
                "image": "alpine",
                "securityContext": {"runAsUser": 1000},
            }
        ],
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert findings == []
    logger.warning.assert_not_called()


def test_one_container_overrides_another_inherits_pod_level_run_as_user() -> None:
    """When only some containers override, only the inheriting ones are flagged."""
    spec = {
        "securityContext": {"runAsUser": 0},
        "containers": [
            {
                "name": "safe",
                "image": "alpine",
                "securityContext": {"runAsUser": 1000},
            },
            {
                "name": "root-by-inheritance",
                "image": "alpine",
            },
        ],
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert len(findings) == 1
    assert "root-by-inheritance" in findings[0]
    assert "safe" not in findings[0]


def test_pod_level_run_as_user_zero_inherited_by_init_container() -> None:
    spec = {
        "securityContext": {"runAsUser": 0},
        "containers": [{"name": "app", "image": "alpine"}],
        "initContainers": [{"name": "setup", "image": "busybox"}],
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert len(findings) == 1
    assert "app" in findings[0]
    assert "setup" in findings[0]


def test_reject_mode_raises_on_pod_level_run_as_user_zero() -> None:
    """_audit_spec_body with reject_high_risk_pod_fields=True must catch the
    pod-level-only root escape path, not just container-level findings."""
    from unittest.mock import MagicMock

    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase

    config = K8sProviderConfig(
        namespace="test",
        audit_high_risk_pod_fields=True,
        reject_high_risk_pod_fields=True,
    )

    class _StubHandler(K8sHandlerBase):
        PROVIDER_API = "Pod"

        async def acquire_hosts(self, request, template):  # type: ignore[override]
            return {}

        def check_hosts_status(self, request):  # type: ignore[override]
            raise NotImplementedError

        async def release_hosts(self, machine_ids, request):  # type: ignore[override]
            pass

        @classmethod
        def get_example_templates(cls):  # type: ignore[override]
            return []

    handler = _StubHandler(
        kubernetes_client=MagicMock(),
        config=config,
        logger=MagicMock(),
    )

    risky_spec = {
        "securityContext": {"runAsUser": 0},
        "containers": [{"name": "app", "image": "alpine"}],
    }

    with pytest.raises(K8sError, match="high-risk"):
        handler._audit_spec_body(risky_spec)


# ---------------------------------------------------------------------------
# SYS_ADMIN capability
# ---------------------------------------------------------------------------


def test_sys_admin_capability_triggers_warning() -> None:
    spec = {
        "containers": [
            {
                "name": "cap-app",
                "image": "alpine",
                "securityContext": {"capabilities": {"add": ["SYS_ADMIN"]}},
            }
        ]
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert len(findings) == 1
    assert "SYS_ADMIN" in findings[0]
    logger.warning.assert_called_once()


def test_net_admin_capability_triggers_warning() -> None:
    spec = {
        "containers": [
            {
                "name": "net-app",
                "image": "alpine",
                "securityContext": {"capabilities": {"add": ["NET_ADMIN"]}},
            }
        ]
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert any("NET_ADMIN" in f for f in findings)


def test_net_raw_capability_triggers_warning() -> None:
    spec = {
        "containers": [
            {
                "name": "raw-app",
                "image": "alpine",
                "securityContext": {"capabilities": {"add": ["NET_RAW"]}},
            }
        ]
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert any("NET_RAW" in f for f in findings)


def test_safe_capability_no_warning() -> None:
    spec = {
        "containers": [
            {
                "name": "safe-app",
                "image": "alpine",
                "securityContext": {"capabilities": {"add": ["CHOWN"]}},
            }
        ]
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    assert findings == []


# ---------------------------------------------------------------------------
# Full pod manifest (with apiVersion / kind) — must descend into spec
# ---------------------------------------------------------------------------


def test_full_pod_manifest_descends_into_spec() -> None:
    manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "test"},
        "spec": {
            "hostNetwork": True,
            "containers": [{"name": "app", "image": "alpine"}],
        },
    }
    logger = _make_logger()
    findings = audit_pod_spec(manifest, logger)
    assert any("hostNetwork" in f for f in findings)


# ---------------------------------------------------------------------------
# Multiple findings in one spec
# ---------------------------------------------------------------------------


def test_multiple_findings_all_reported() -> None:
    spec = {
        "hostNetwork": True,
        "hostPID": True,
        "volumes": [{"name": "v", "hostPath": {"path": "/tmp"}}],
        "containers": [
            {
                "name": "app",
                "image": "alpine",
                "securityContext": {
                    "privileged": True,
                    "capabilities": {"add": ["SYS_ADMIN", "NET_RAW"]},
                },
            }
        ],
    }
    logger = _make_logger()
    findings = audit_pod_spec(spec, logger)
    # hostNetwork, hostPID, hostPath, privileged, SYS_ADMIN, NET_RAW = 6
    assert len(findings) == 6
    assert logger.warning.call_count == 6


# ---------------------------------------------------------------------------
# Reject mode via K8sHandlerBase._audit_spec_body
# ---------------------------------------------------------------------------


def test_reject_mode_raises_k8s_error_on_host_path() -> None:
    """_audit_spec_body with reject_high_risk_pod_fields=True must raise K8sError."""
    from unittest.mock import MagicMock

    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase

    config = K8sProviderConfig(
        namespace="test",
        audit_high_risk_pod_fields=True,
        reject_high_risk_pod_fields=True,
    )

    # Minimal concrete subclass — we only need _audit_spec_body.
    class _StubHandler(K8sHandlerBase):
        PROVIDER_API = "Pod"

        async def acquire_hosts(self, request, template):  # type: ignore[override]
            return {}

        def check_hosts_status(self, request):  # type: ignore[override]
            raise NotImplementedError

        async def release_hosts(self, machine_ids, request):  # type: ignore[override]
            pass

        @classmethod
        def get_example_templates(cls):  # type: ignore[override]
            return []

    handler = _StubHandler(
        kubernetes_client=MagicMock(),
        config=config,
        logger=MagicMock(),
    )

    risky_spec = {
        "volumes": [{"name": "vol", "hostPath": {"path": "/etc"}}],
        "containers": [{"name": "app", "image": "alpine"}],
    }

    with pytest.raises(K8sError, match="high-risk"):
        handler._audit_spec_body(risky_spec)


def test_reject_mode_off_no_raise_on_host_path() -> None:
    """Default config (reject=False) must not raise even when findings exist."""
    from unittest.mock import MagicMock

    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase

    config = K8sProviderConfig(
        namespace="test",
        audit_high_risk_pod_fields=True,
        reject_high_risk_pod_fields=False,  # default — warn only
    )

    class _StubHandler(K8sHandlerBase):
        PROVIDER_API = "Pod"

        async def acquire_hosts(self, request, template):  # type: ignore[override]
            return {}

        def check_hosts_status(self, request):  # type: ignore[override]
            raise NotImplementedError

        async def release_hosts(self, machine_ids, request):  # type: ignore[override]
            pass

        @classmethod
        def get_example_templates(cls):  # type: ignore[override]
            return []

    handler = _StubHandler(
        kubernetes_client=MagicMock(),
        config=config,
        logger=MagicMock(),
    )

    risky_spec = {
        "hostNetwork": True,
        "containers": [{"name": "app", "image": "alpine"}],
    }

    # Must not raise.
    handler._audit_spec_body(risky_spec)


def test_audit_disabled_skips_check() -> None:
    """audit_high_risk_pod_fields=False must skip the audit entirely."""
    from unittest.mock import MagicMock

    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase

    config = K8sProviderConfig(
        namespace="test",
        audit_high_risk_pod_fields=False,
        reject_high_risk_pod_fields=True,  # would raise if audit ran
    )

    class _StubHandler(K8sHandlerBase):
        PROVIDER_API = "Pod"

        async def acquire_hosts(self, request, template):  # type: ignore[override]
            return {}

        def check_hosts_status(self, request):  # type: ignore[override]
            raise NotImplementedError

        async def release_hosts(self, machine_ids, request):  # type: ignore[override]
            pass

        @classmethod
        def get_example_templates(cls):  # type: ignore[override]
            return []

    handler = _StubHandler(
        kubernetes_client=MagicMock(),
        config=config,
        logger=MagicMock(),
    )

    risky_spec = {
        "hostNetwork": True,
        "containers": [{"name": "app", "image": "alpine"}],
    }

    # Must not raise because audit is disabled.
    handler._audit_spec_body(risky_spec)


# ---------------------------------------------------------------------------
# Workload kinds: the pod spec lives under spec.template.spec
# ---------------------------------------------------------------------------


def _workload(kind: str, pod_spec: dict, snake_case: bool = False) -> dict:
    api_versions = {"Job": "batch/v1", "Deployment": "apps/v1", "StatefulSet": "apps/v1"}
    api_key = "api_version" if snake_case else "apiVersion"
    return {
        api_key: api_versions[kind],
        "kind": kind,
        "spec": {"template": {"spec": pod_spec}},
    }


@pytest.mark.parametrize("kind", ["Job", "Deployment", "StatefulSet"])
@pytest.mark.parametrize("snake_case", [False, True])
def test_workload_pod_level_run_as_user_zero_triggers_warning(kind: str, snake_case: bool) -> None:
    sc_key = "security_context" if snake_case else "securityContext"
    run_as_key = "run_as_user" if snake_case else "runAsUser"
    pod_spec = {
        sc_key: {run_as_key: 0},
        "containers": [{"name": "orb", "image": "busybox:latest"}],
    }
    findings = audit_pod_spec(_workload(kind, pod_spec, snake_case), _make_logger())
    assert len(findings) == 1
    assert "runAsUser = 0" in findings[0]


@pytest.mark.parametrize("kind", ["Job", "Deployment", "StatefulSet"])
def test_workload_container_level_findings_reported(kind: str) -> None:
    pod_spec = {
        "hostNetwork": True,
        "containers": [
            {
                "name": "orb",
                "securityContext": {"privileged": True, "runAsUser": 0},
            }
        ],
    }
    findings = audit_pod_spec(_workload(kind, pod_spec), _make_logger())
    joined = "\n".join(findings)
    assert "hostNetwork" in joined
    assert "privileged = True" in joined
    assert "runAsUser = 0" in joined


@pytest.mark.parametrize("kind", ["Job", "Deployment", "StatefulSet"])
def test_workload_clean_pod_template_no_warning(kind: str) -> None:
    pod_spec = {
        "securityContext": {"runAsUser": 1000},
        "containers": [{"name": "orb", "image": "busybox:latest"}],
    }
    assert audit_pod_spec(_workload(kind, pod_spec), _make_logger()) == []


def test_cron_job_pod_level_run_as_user_zero_triggers_warning() -> None:
    body = {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "spec": {
            "jobTemplate": {
                "spec": {
                    "template": {
                        "spec": {
                            "securityContext": {"runAsUser": 0},
                            "containers": [{"name": "orb"}],
                        }
                    }
                }
            }
        },
    }
    assert len(audit_pod_spec(body, _make_logger())) == 1
