"""Worker for the cross-process storage tests; run as ``python -I _concurrent_worker.py ...``.

Usage: backend workdir mode prefix count barrier [request_id]

Modes:
  distinct  create ``count`` new requests, one unit of work each
  counter   append ``count`` resource ids to the existing request ``request_id``,
            reloading and retrying when another process wrote first

Prints a JSON object with the number of conflicts that were retried.
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType

_ENGINE = None


def make_uow(backend: str, workdir: str):
    global _ENGINE
    if backend == "json":
        from orb.infrastructure.storage.json.unit_of_work import JSONUnitOfWork

        return JSONUnitOfWork(data_dir=workdir, backup_enabled=False)
    if backend == "sql":
        from sqlalchemy import create_engine

        from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork

        if _ENGINE is None:
            _ENGINE = create_engine(f"sqlite:///{workdir}/orb.db", connect_args={"timeout": 120})
        return SQLUnitOfWork(_ENGINE)
    if backend == "dynamodb":
        from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
        from orb.providers.aws.storage.unit_of_work import DynamoDBUnitOfWork

        return DynamoDBUnitOfWork(
            aws_client=None, logger=LoggingAdapter("test.worker"), region="us-east-1"
        )
    raise SystemExit(f"unknown backend {backend}")


def wait_for(barrier: str) -> None:
    deadline = time.monotonic() + 120
    while not Path(barrier).exists():
        if time.monotonic() > deadline:
            raise SystemExit("barrier timeout")
        time.sleep(0.005)


def main() -> None:
    backend, workdir, mode, prefix, count, barrier = sys.argv[1:7]
    uow = make_uow(backend, workdir)
    retries = 0
    wait_for(barrier)

    if mode == "distinct":
        for _ in range(int(count)):
            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id="tmpl-1",
                machine_count=1,
                provider_type="aws",
                provider_name="aws-default",
                provider_api="RunInstances",
            )
            with uow:
                uow.requests.save(request)
            with open(Path(workdir) / f"ids-{prefix}.txt", "a", encoding="utf-8") as out:
                out.write(f"{request.request_id}\n")
    else:
        request_id = sys.argv[7]
        for n in range(int(count)):
            for _attempt in range(1000):
                try:
                    with uow:
                        loaded = uow.requests.find_by_id(request_id)
                        assert loaded is not None
                        uow.requests.save(loaded.add_resource_id(f"{prefix}-{n}"))
                    break
                except ConcurrencyError:
                    retries += 1
                    uow = make_uow(
                        backend, workdir
                    )  # a unit of work is not reused after a conflict
                    time.sleep(random.uniform(0, 0.01))
            else:
                raise SystemExit("gave up after 1000 conflicts")

    print(json.dumps({"retries": retries}))


if __name__ == "__main__":
    main()
