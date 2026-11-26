"""Shared fixtures for the k8s provider test tree.

The k8s handlers wrap kubernetes-client calls in the resilience retry layer.
Whether an error is retried is decided by the *retry classifier registry*: the
``K8sRetryClassifier`` marks 404/400/401/403 (and similar terminal API errors)
as non-retryable so the handler fails fast instead of retrying.

In production that classifier is registered during provider bootstrap
(``K8sProviderStrategy.initialize`` → ``registration.py``).  Unit and mocked
tests construct handlers directly and never run that bootstrap, so without this
fixture the registry is empty: every exception — including the 404s that
mocked tests deliberately provoke — is treated as retryable and retried with
real ``time.sleep`` backoff (``base_delay`` defaults to 1s).  A handful of
tests then spend seconds sleeping through retries they should never attempt,
dominating the k8s suite's wall-clock.

Registering the classifier for the whole k8s tree makes those terminal errors
fail fast (matching production behaviour) and removes the spurious retry
sleeps.  It is autouse + function-scoped with a clean teardown so no test
leaks classifier state into another.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_shared_kube_clients():
    """Drop init-scoped kubeconfig/ApiClient caches so tests never see each other's clients."""
    from orb.providers.k8s.auth.kubeconfig import clear_shared_kube_state

    clear_shared_kube_state()
    yield
    clear_shared_kube_state()


@pytest.fixture(autouse=True)
def _reset_k8s_default_configuration():
    """Clear the kubernetes client's process-global default ``Configuration``.

    ``Configuration._default`` is class-level state that ORB's auth layer sets
    (``auth/in_cluster.py``, ``auth/kubeconfig.py`` both call
    ``Configuration.set_default``).  Once it is populated,
    ``Configuration.get_default_copy`` switches from constructing a fresh
    config to ``copy.deepcopy(cls._default)`` — and every k8s SDK model
    constructor calls ``get_default_copy``.  Tests asserting that a read-only
    code path performs no deepcopy therefore pass or fail based on whether an
    earlier test in the same xdist worker happened to set a default, which is
    nondeterministic under ``-n auto``.  Reset per test, restore on teardown.
    """
    from kubernetes.client import Configuration  # type: ignore[reportAttributeAccessIssue]

    # Assign the attribute directly rather than via ``set_default``, which
    # deep-copies its argument — teardown must restore the *same* object a
    # caller outside this tree may still hold a reference to.
    saved = getattr(Configuration, "_default", None)
    Configuration._default = None
    yield
    Configuration._default = saved


@pytest.fixture(autouse=True)
def _register_k8s_retry_classifier():
    """Register K8sRetryClassifier for each k8s test (mirrors provider bootstrap)."""
    from orb.infrastructure.resilience.retry_classifier_registry import (
        clear_classifiers,
        register_retry_classifier,
    )
    from orb.providers.k8s.resilience.retry_classifier import K8sRetryClassifier

    register_retry_classifier(K8sRetryClassifier())
    yield
    clear_classifiers()


@pytest.fixture(autouse=True)
def _isolate_k8s_handler_plugins():
    """Run each test with an empty plugin-handler registry; restore it afterwards.

    ``register_k8s_provider`` registers the custom-resource handlers that ship
    with ORB (``KubeSwiftSandbox``) in the process-wide registry of
    :mod:`orb.providers.k8s.handler_plugins`.  A test that bootstraps the
    provider would otherwise leak them into every later test in the worker,
    so lists of supported APIs would depend on test order.
    """
    from orb.providers.k8s import handler_plugins

    saved = dict(handler_plugins._PLUGIN_HANDLERS)
    handler_plugins._PLUGIN_HANDLERS.clear()
    yield
    handler_plugins._PLUGIN_HANDLERS.clear()
    handler_plugins._PLUGIN_HANDLERS.update(saved)
