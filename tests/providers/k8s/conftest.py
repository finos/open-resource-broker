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
