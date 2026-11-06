"""Keep botocore's own retries out of calls the AWS call executor retries.

EC2 and Auto Scaling clients are shared by two kinds of callers. Calls routed
through :class:`~orb.providers.aws.resilience.call_executor.AWSCallExecutor` are
retried by ORB, with classification, idempotency tokens and circuit breakers, and
must make one HTTP request per attempt. Every other call has no ORB retry and
keeps botocore's. A client therefore keeps botocore retries enabled, and this
gate switches them off for the duration of an executor call.

botocore decides on a retry through its ``needs-retry`` event, taking the first
non-``None`` answer; an answer of ``False`` means "do not retry". The gate is
registered ahead of botocore's own handler and answers ``False`` while an executor
call is active in the same thread or task, and ``None`` (no opinion) otherwise.
Older botocore releases treat ``False`` as a zero-second retry delay, so the gate is
only installed from botocore 1.43.3, the first release that does.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Optional

import botocore

# First botocore release whose Endpoint._needs_retry treats False as "do not retry".
MIN_BOTOCORE_VERSION = (1, 43, 3)

_executor_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "aws_call_executor_active", default=False
)


@contextmanager
def executor_call() -> Iterator[None]:
    """Mark the enclosed AWS calls as retried by the executor."""
    token = _executor_active.set(True)
    try:
        yield
    finally:
        _executor_active.reset(token)


def _gate(**kwargs: Any) -> Optional[bool]:
    return False if _executor_active.get() else None


def _botocore_understands_false() -> bool:
    """True when the installed botocore reads a ``False`` needs-retry answer as "no retry"."""
    try:
        parts = tuple(int(p) for p in botocore.__version__.split(".")[:3])
    except (AttributeError, ValueError):
        return False
    return parts >= MIN_BOTOCORE_VERSION


def install_retry_gate(client: Any) -> bool:
    """Make *client* skip botocore retries inside executor calls.

    Returns False, without installing anything, when the installed botocore
    cannot honour the gate; the caller must then build a client with botocore
    retries off instead, so the executor's retries are never stacked on botocore's.
    """
    if not _botocore_understands_false():
        return False
    try:
        # Registered on the same event prefix as botocore's own handler (it is looked
        # up most-specific first), and ahead of it within that prefix.
        service_id = client.meta.service_model.service_id.hyphenize()
        client.meta.events.register_first(f"needs-retry.{service_id}", _gate)
    except Exception:
        return False
    return True
