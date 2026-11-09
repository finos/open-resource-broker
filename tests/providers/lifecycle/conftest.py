"""Fixtures for the lifecycle invariant suite."""

from __future__ import annotations

import pytest

# Provider-owned AWS fixtures: fake credentials, moto, VPC resources and the
# singleton reset that every container-backed test relies on.
from tests.providers.aws.conftest import (  # noqa: F401
    _aws_fake_credentials,
    _clear_shared_aws_sessions,
    _ignore_ambient_aws_profile,
    moto_aws,
    moto_vpc_resources,
    orb_config_dir as _aws_orb_config_dir,
    reset_singletons_aws,
)
from tests.providers.k8s.mocked import conftest as _k8s_mocked

kmock_k8s = _k8s_mocked.kmock_k8s
orb_config_dir_k8s = _k8s_mocked.orb_config_dir_k8s


@pytest.fixture
def request_timeout() -> int:
    """Seconds a request may take before its deadline; override per module."""
    return 3600


@pytest.fixture
def aws_config_dir(request, request_timeout):
    import json

    config_dir = request.getfixturevalue("_aws_orb_config_dir")

    cfg_path = config_dir / "config.json"
    cfg = json.loads(cfg_path.read_text())
    for provider in cfg["provider"]["providers"]:
        provider.get("config", {}).pop("profile", None)
    cfg.setdefault("request", {})["default_timeout"] = request_timeout
    cfg_path.write_text(json.dumps(cfg, indent=2))
    # One machine per requested unit, so a requested count is a machine count.
    templates_path = config_dir / "aws_templates.json"
    templates = json.loads(templates_path.read_text())
    for template in templates["templates"]:
        template["vmTypes"] = {"t3.medium": 1}
    templates_path.write_text(json.dumps(templates, indent=2))
    default_path = config_dir / "default_config.json"
    if default_path.exists():
        d = json.loads(default_path.read_text())
        try:
            d["provider"]["provider_defaults"]["aws"]["template_defaults"]["image_id"] = (
                "ami-12345678"
            )
        except (KeyError, TypeError):
            return config_dir
        default_path.write_text(json.dumps(d, indent=2))
    return config_dir


# ---------------------------------------------------------------------------
# Time: one fake clock per test, sleeps routed to it, real sleeps fail the test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def lifecycle_clock(monkeypatch):
    """A fresh FakeClock at offset 0 for every test, whatever the container scope."""
    from tests.providers.lifecycle.clocking import FakeClock, RealSleepGuard, route_sleeps

    clock = FakeClock()
    guard = RealSleepGuard()
    guard.install(monkeypatch)
    route_sleeps(monkeypatch, clock)
    yield clock
    assert not guard.violations, f"real sleeps longer than 50 ms ran: {guard.violations}"


# ---------------------------------------------------------------------------
# Container-backed driver, one per test and cell
# ---------------------------------------------------------------------------


@pytest.fixture
def life(request, lifecycle_clock, monkeypatch):
    """Lifecycle driver for the cell given by indirect parametrization."""
    cell = request.param
    if cell.provider == "aws":
        return request.getfixturevalue("_aws_life")
    return request.getfixturevalue("_k8s_life")


@pytest.fixture
def _aws_life(request, aws_config_dir, lifecycle_clock, monkeypatch):
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.infrastructure.di.container import get_container
    from orb.providers.registry import get_provider_registry
    from tests.providers.aws.conftest import REGION, make_patch_moto_compat
    from tests.providers.lifecycle.aws_cloud import AwsCloud
    from tests.providers.lifecycle.driver import Lifecycle, register_clock

    cell = request.node.callspec.params["life"]
    with make_patch_moto_compat():
        cloud = AwsCloud(REGION)
        cloud.install(monkeypatch)
        container = get_container()
        registry = get_provider_registry()
        registry._config_port = container.get(ConfigurationPort)
        for instance in registry._config_port.get_provider_config().get_active_providers():
            registry.ensure_provider_instance_registered_from_config(instance)
        register_clock(container, lifecycle_clock)
        yield Lifecycle(cell, cloud, lifecycle_clock, container)


@pytest.fixture
def _k8s_life(request, orb_config_dir_k8s, kmock_k8s, lifecycle_clock, monkeypatch):
    from kubernetes.client import Configuration

    from orb.infrastructure.di.container import get_container
    from orb.infrastructure.resilience.retry_classifier_registry import (
        clear_classifiers,
        register_retry_classifier,
    )
    from orb.providers.k8s.auth.kubeconfig import clear_shared_kube_state
    from orb.providers.k8s.resilience.retry_classifier import K8sRetryClassifier
    from orb.providers.registry import get_provider_registry
    from tests.providers.k8s.mocked.conftest import K8S_KMOCK_PROVIDER_NAME
    from tests.providers.k8s.mocked.kmock_delivery_conftest import (
        _inject_kmock_factory,
        _make_k8s_logger,
        _register_deployment_resource,
        _register_pod_resource,
    )
    from tests.providers.lifecycle.driver import Lifecycle, register_clock
    from tests.providers.lifecycle.k8s_cloud import K8sCloud

    cell = request.node.callspec.params["life"]
    clear_shared_kube_state()
    saved_default = getattr(Configuration, "_default", None)
    Configuration._default = None
    register_retry_classifier(K8sRetryClassifier())
    _register_pod_resource(kmock_k8s)
    _register_deployment_resource(kmock_k8s)
    container = get_container()
    _inject_kmock_factory(kmock_k8s, _make_k8s_logger())
    strategy = get_provider_registry().get_or_create_strategy(K8S_KMOCK_PROVIDER_NAME)
    cloud = K8sCloud(kmock_k8s)
    cloud.install(strategy.kubernetes_client.api_client)
    register_clock(container, lifecycle_clock)
    # Handlers read time through their own clock, and a status cache would serve a
    # view from before the last step; the suite wants every read to be fresh.
    strategy._k8s_config.controller_status_cache_ttl_seconds = 0
    for handler in _handlers(strategy):
        handler._clock = lifecycle_clock
    life = Lifecycle(cell, cloud, lifecycle_clock, container)
    # The Deployment cell's one known deviation is pinned by a single strict test;
    # every other scenario skips that one row so it can exercise its own invariant.
    life.scenario.tolerate_resource_rows = cell.api == "Deployment"
    yield life
    clear_classifiers()
    clear_shared_kube_state()
    Configuration._default = saved_default


def _handlers(strategy):
    registry = strategy._handler_registry
    return [registry.get_handler(api) for api in ("Pod", "Deployment")]


# ---------------------------------------------------------------------------
# Session end: every invariant must have been exercised by some scenario
# ---------------------------------------------------------------------------


def _lifecycle_modules() -> set[str]:
    from pathlib import Path

    return {p.name for p in Path(__file__).parent.glob("test_*.py")}


def pytest_collection_finish(session):
    """Remember which lifecycle modules this process collected."""
    import os

    from tests.providers.lifecycle.oracle import counter_dir

    names = sorted({i.path.name for i in session.items if "lifecycle" in i.path.parts})
    if names:
        directory = counter_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"modules-{os.getpid()}.txt").write_text("\n".join(names))


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    import shutil

    from tests.providers.lifecycle.oracle import (
        INVARIANT_IDS,
        UNEXERCISED_UNTIL_BEHAVIOUR,
        counter_dir,
        exercised_counts,
    )

    if hasattr(session.config, "workerinput"):
        return
    directory = counter_dir()
    try:
        modules: set[str] = set()
        for path in directory.glob("modules-*.txt"):
            modules |= set(path.read_text().split())
        if exitstatus == 0 and modules and _lifecycle_modules() <= modules:
            counts = exercised_counts()
            never = [
                i for i in INVARIANT_IDS if not counts[i] and i not in UNEXERCISED_UNTIL_BEHAVIOUR
            ]
            stale = [i for i in UNEXERCISED_UNTIL_BEHAVIOUR if counts[i]]
            if never or stale:
                _report_exercise_failure(
                    session,
                    f"lifecycle invariants never exercised: {never}; "
                    f"exempt but now exercised (remove the exemption): {stale}",
                )
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _report_exercise_failure(session, message: str) -> None:
    """Fail the run and record the failure as a test case, so it also shows in junit."""
    from _pytest.reports import TestReport

    session.exitstatus = 1
    report = TestReport(
        nodeid="tests/providers/lifecycle::every_invariant_is_exercised",
        location=("tests/providers/lifecycle", None, "every_invariant_is_exercised"),
        keywords={},
        outcome="failed",
        longrepr=message,
        when="call",
    )
    session.config.hook.pytest_runtest_logreport(report=report)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(message, red=True)


def pytest_collection_modifyitems(config, items):
    """Backstop: no lifecycle scenario may run longer than a minute."""
    for item in items:
        if "lifecycle" in item.path.parts and item.get_closest_marker("timeout") is None:
            item.add_marker(pytest.mark.timeout(60))
