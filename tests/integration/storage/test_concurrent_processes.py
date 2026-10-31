"""Real concurrent processes against each storage backend.

Every writer is a separate Python process released at the same moment. The checks
are the ones that matter for correctness: no record is lost, no update is lost,
and the stored version counts exactly the writes that succeeded.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType

WORKER = Path(__file__).with_name("_concurrent_worker.py")
PROCESSES = 6
PER_PROCESS = 4

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(params=["json", "sql", "dynamodb"])
def backend(request, tmp_path, monkeypatch):
    """Yield ``(name, make_uow, env)`` for a backend whose storage the workers share."""
    name = request.param
    env: dict[str, str] = {}
    if name == "dynamodb":
        pytest.importorskip("flask", reason="the moto server needs moto[server]")
        from moto.server import ThreadedMotoServer

        port = _free_port()
        server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
        server.start()
        env = {
            "AWS_ENDPOINT_URL": f"http://127.0.0.1:{port}",
            "AWS_ACCESS_KEY_ID": "testing",
            "AWS_SECRET_ACCESS_KEY": "testing",  # noqa: S105  # fake credential for the local moto server
            "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_EC2_METADATA_DISABLED": "true",
        }
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        try:
            yield name, _make_uow(name, tmp_path), env
        finally:
            server.stop()
        return
    yield name, _make_uow(name, tmp_path), env


def _make_uow(name: str, workdir: Path):
    if name == "json":
        from orb.infrastructure.storage.json.unit_of_work import JSONUnitOfWork

        return JSONUnitOfWork(data_dir=str(workdir), backup_enabled=False)
    if name == "sql":
        from sqlalchemy import create_engine

        from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork

        return SQLUnitOfWork(create_engine(f"sqlite:///{workdir}/orb.db"))
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.aws.storage.unit_of_work import DynamoDBUnitOfWork

    return DynamoDBUnitOfWork(
        aws_client=None, logger=LoggingAdapter("test.parent"), region="us-east-1"
    )


def _run_workers(name: str, workdir: Path, env: dict[str, str], mode: str, *extra: str) -> int:
    """Start the workers, release them together, return the conflicts they retried."""
    import os

    barrier = workdir / "go"
    barrier.unlink(missing_ok=True)
    child_env = {**os.environ, **env}
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-I",
                str(WORKER),
                name,
                str(workdir),
                mode,
                f"p{i}",
                str(PER_PROCESS),
                str(barrier),
                *extra,
            ],
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(PROCESSES)
    ]
    time.sleep(1.0 if name == "dynamodb" else 0.3)  # let every worker reach the barrier
    barrier.touch()
    retries = 0
    for proc in procs:
        out, err = proc.communicate(timeout=300)
        assert proc.returncode == 0, f"worker failed:\n{err}"
        retries += json.loads(out.strip().splitlines()[-1])["retries"]
    return retries


def test_concurrent_writers_of_different_requests_lose_none(backend, tmp_path):
    name, parent_uow, env = backend

    _run_workers(name, tmp_path, env, "distinct")

    expected = {line for path in tmp_path.glob("ids-*.txt") for line in path.read_text().split()}
    assert len(expected) == PROCESSES * PER_PROCESS
    stored = {str(r.request_id) for r in parent_uow.requests.find_all()}
    assert expected <= stored


def test_concurrent_read_modify_write_loses_no_update(backend, tmp_path):
    name, parent_uow, env = backend
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tmpl-1",
        machine_count=1,
        provider_type="aws",
        provider_name="aws-default",
        provider_api="RunInstances",
    )
    with parent_uow:
        parent_uow.requests.save(request)
    request_id = str(request.request_id)

    _run_workers(name, tmp_path, env, "counter", request_id)

    final = parent_uow.requests.find_by_id(request_id)
    assert final is not None
    expected_ids = {f"p{i}-{n}" for i in range(PROCESSES) for n in range(PER_PROCESS)}
    assert set(final.resource_ids) == expected_ids
    # Every successful write moved the stored version by exactly one
    assert final.version == PROCESSES * PER_PROCESS
