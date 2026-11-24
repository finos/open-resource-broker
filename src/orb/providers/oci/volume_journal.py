"""Durable, write-once records for data volumes owned by an OCI acquire request."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from orb.domain.base import UnitOfWork
from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.request.aggregate import Request
from orb.domain.request.fulfilment_state_machine import FulfilmentEvent, FulfilmentStateMachine
from orb.domain.request.request_types import RequestStatus, RequestType
from orb.domain.request.value_objects import RequestId

_PREFIX = "oci_secondary_volumes/"
# OCI tokens can expire or be invalidated earlier. Never replace a rejected token.
_REPLAY_SECONDS = 23 * 60 * 60


@dataclass(frozen=True)
class VolumeSlot:
    index: int
    spec: dict[str, Any]
    compartment_id: str
    availability_domain: str
    returning: bool
    create_started: bool
    attach_started: bool
    volume_id: str | None
    attachment_id: str | None
    outcome: str | None
    attached_confirmed: bool = False


class OCIVolumeJournal:
    """Persist independent immutable keys; ORB acquire writes shallow-merge these."""

    def __init__(
        self,
        create_unit_of_work: Callable[[], UnitOfWork],
        provider_name: str,
        region: str,
        *,
        clock: Callable[[], float] = time.time,
        state_machine: Callable[[], FulfilmentStateMachine] | None = None,
    ) -> None:
        self._create_uow = create_unit_of_work
        self._provider = provider_name
        self._region = region
        self._clock = clock
        self._state_machine = state_machine or (
            lambda: FulfilmentStateMachine(grace_period_seconds=3600)
        )
        self.on_change: Callable[[str, str], None] | None = None

    @staticmethod
    def plan_key(worker_id: str) -> str:
        if not worker_id or "/" in worker_id:
            raise ValueError("Invalid worker ID for OCI secondary volumes")
        return f"{_PREFIX}{worker_id}/plan"

    @staticmethod
    def has_plans(provider_data: dict[str, Any]) -> bool:
        return any(key.startswith(_PREFIX) and key.endswith("/plan") for key in provider_data)

    def _belongs(self, row: Request) -> bool:
        return (
            row.request_type == RequestType.ACQUIRE
            and row.provider_type == "oci"
            and row.provider_name == self._provider
        )

    def _read(self, uow: UnitOfWork, request_id: str) -> Request:
        row = uow.requests.get_by_id(RequestId(value=request_id))
        if row is None or not self._belongs(row):
            raise ValueError(
                "OCI secondary volumes require the original acquire request for this provider"
            )
        return row

    def _store(self) -> UnitOfWork:
        uow = self._create_uow()
        # The built-in JSON backend uses flock for interprocess version checks.
        # Windows JSON and SQLite in-memory databases cannot support this ledger.
        from orb.infrastructure.storage.json.unit_of_work import JSONUnitOfWork
        from orb.infrastructure.storage.repositories.versioned_save import supports_versioned_save
        from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork

        if not supports_versioned_save(getattr(uow.requests, "storage_strategy", None)):
            raise ValueError("OCI secondary volumes require version-checked request storage")
        if os.name == "nt" and isinstance(uow, JSONUnitOfWork):
            raise ValueError("OCI secondary volumes require file-backed SQL storage on Windows")
        if isinstance(uow, SQLUnitOfWork) and uow.engine.dialect.name == "sqlite":
            url = uow.engine.url
            if not url.database or ":memory:" in url.database or url.query.get("mode") == "memory":
                raise ValueError("OCI secondary volumes require persistent request storage")
        return uow

    def _active(self, row: Request) -> None:
        if not row.status.is_active() or (
            row.deadline_at and row.deadline_at.timestamp() <= self._clock()
        ):
            raise ValueError("OCI volume acquisition has ended or reached its deadline")

    def require_acquisition(self, request_id: str) -> None:
        self._state_machine()  # resolve the configured deadline policy before launching a worker
        with self._store() as uow:
            self._active(self._read(uow, request_id))

    def _update(self, request_id: str, operation: Callable[[Request], Request | None]) -> None:
        # A conflicting write must re-evaluate the entire operation against the
        # latest revision, including cancellation and any other worker's receipts.
        for attempt in range(4):
            try:
                with self._store() as uow:
                    updated = operation(self._read(uow, request_id))
                    if updated is not None:
                        uow.requests.save(updated)
                return
            except ConcurrencyError:
                if attempt == 3:
                    raise

    def _write(self, request_id: str, key: str, operation: Callable[[Request], Any]) -> Any:
        value = None

        def insert(row: Request) -> Request | None:
            nonlocal value
            value = operation(row)
            if key in row.provider_data:
                if row.provider_data[key] != value:
                    raise ValueError("Conflicting immutable OCI volume record")
                return None
            return row.set_provider_data({**row.provider_data, key: value})

        self._update(request_id, insert)
        return value

    def begin(
        self,
        request_id: str,
        worker_id: str,
        compartment_id: str,
        availability_domain: str,
        volumes: list[dict[str, Any]],
        *,
        instance_type: str | None = None,
        image_id: str | None = None,
    ) -> bool:
        if not compartment_id or not availability_domain or not volumes:
            raise ValueError("OCI secondary volumes require placement and a nonempty plan")
        if any(type(spec.get("delete_on_return")) is not bool for spec in volumes):
            raise ValueError("Every OCI secondary volume must specify delete_on_return")
        key = self.plan_key(worker_id)

        created = False

        expected = {
            "region": self._region,
            "compartment_id": compartment_id,
            "availability_domain": availability_domain,
            "volumes": volumes,
        }

        if instance_type:
            expected["instance_type"] = instance_type
        if image_id:
            expected["image_id"] = image_id

        def checkpoint(row: Request) -> Request | None:
            nonlocal created
            created = key not in row.provider_data
            if not created:
                if row.provider_data[key] != expected or worker_id not in row.resource_ids:
                    raise ValueError("Conflicting immutable OCI worker plan or resource checkpoint")
                if key + "/return" in row.provider_data:
                    raise ValueError("OCI volume return has started")
                return None
            data = {**row.provider_data, key: expected}
            if worker_id in row.resource_ids or worker_id in row.machine_ids:
                data[key + "/accepted"] = True
            # Save the signed launch receipt even if cancellation raced the cloud
            # response. Terminal requests stay terminal and can only clean up.
            updated = row.add_resource_id(worker_id).set_provider_data(data)
            if row.status == RequestStatus.PENDING:
                machine = self._state_machine()
                updated = machine.apply(
                    updated, FulfilmentEvent.RESOURCES_CREATED, now=machine.now()
                )
            return updated

        self._update(request_id, checkpoint)
        if self.on_change is not None:
            self.on_change(request_id, worker_id)
        return created

    def worker_details(self, request_id: str, worker_id: str) -> dict[str, str]:
        """Launch-time facts for normal broker machine sync after an interrupted acquire."""
        key = self.plan_key(worker_id)
        with self._store() as uow:
            row = self._read(uow, request_id)
            if worker_id not in row.resource_ids or not self._slots(row, worker_id):
                raise ValueError("Missing worker checkpoint for OCI volume recovery")
            return {
                name: row.provider_data[key][name]
                for name in ("instance_type", "image_id")
                if isinstance(row.provider_data[key].get(name), str)
                and row.provider_data[key][name]
            }

    def accept_workers(self, request_id: str, workers: list[str]) -> None:
        """Fence partial-batch rollback before handing workers back to the acquire flow."""

        def accept(row: Request) -> Request | None:
            self._active(row)
            updated = dict(row.provider_data)
            for worker in workers:
                key = self.plan_key(worker)
                if (
                    worker not in row.resource_ids
                    or not self._slots(row, worker)
                    or key + "/return" in updated
                ):
                    raise ValueError("OCI worker plan is unavailable for handoff")
                updated[key + "/accepted"] = True
            return None if updated == row.provider_data else row.set_provider_data(updated)

        self._update(request_id, accept)

    def _slots(self, row: Request, worker_id: str) -> list[VolumeSlot]:
        key = self.plan_key(worker_id)
        data = row.provider_data
        if key not in data:
            return []
        plan = data[key]
        if plan["region"] != self._region:
            raise ValueError("OCI volume plan belongs to a different region")
        slots = []
        for index, spec in enumerate(plan["volumes"]):
            prefix = f"{key}/{index}"
            if type(spec.get("delete_on_return")) is not bool:
                raise ValueError("Invalid retained OCI volume policy")
            slots.append(
                VolumeSlot(
                    index,
                    dict(spec),
                    plan["compartment_id"],
                    plan["availability_domain"],
                    key + "/return" in data,
                    prefix + "/create" in data,
                    prefix + "/attach" in data,
                    data.get(prefix + "/volume"),
                    data.get(prefix + "/attachment"),
                    data.get(prefix + "/outcome"),
                    prefix + "/attached" in data,
                )
            )
        for slot in slots:
            if slot.outcome is not None:
                expected = "deleted" if slot.spec["delete_on_return"] else "retained"
                if not slot.create_started:
                    expected = "skipped"
                if (
                    not slot.returning
                    or slot.outcome != expected
                    or (slot.create_started and not slot.volume_id)
                ):
                    raise ValueError("Invalid terminal OCI volume record")
            if slot.attached_confirmed and not slot.attachment_id:
                raise ValueError("Invalid OCI attachment confirmation")
            if (
                slot.volume_id is not None
                and (
                    not slot.create_started
                    or not isinstance(slot.volume_id, str)
                    or not slot.volume_id.strip()
                )
            ) or (
                slot.attachment_id is not None
                and (
                    not slot.volume_id
                    or not slot.attach_started
                    or not isinstance(slot.attachment_id, str)
                    or not slot.attachment_id.strip()
                )
            ):
                raise ValueError("Invalid OCI secondary-volume receipt")
            for kind, identifier in (
                ("volume", slot.volume_id),
                ("attachment", slot.attachment_id),
            ):
                if (
                    identifier
                    and sum(
                        v == identifier
                        for k, v in data.items()
                        if k.startswith(_PREFIX) and k.endswith("/" + kind)
                    )
                    != 1
                ):
                    raise ValueError("Conflicting OCI secondary-volume ownership")
        return slots

    def slots(self, request_id: str, worker_id: str) -> list[VolumeSlot]:
        with self._store() as uow:
            return self._slots(self._read(uow, request_id), worker_id)

    def _slot(self, row: Request, worker_id: str, index: int) -> VolumeSlot:
        slots = self._slots(row, worker_id)
        if not 0 <= index < len(slots):
            raise ValueError("No matching OCI volume slot on the original request")
        return slots[index]

    def retry_token(
        self, request_id: str, worker_id: str, index: int, action: Literal["create", "attach"]
    ) -> str:
        if action not in ("create", "attach"):
            raise ValueError("Unknown OCI secondary-volume operation")
        key = self.plan_key(worker_id)
        intent_key = f"{key}/{index}/{action}"

        def intent(row: Request) -> dict[str, Any]:
            self._active(row)
            slot = self._slot(row, worker_id, index)
            if key + "/return" in row.provider_data or slot.outcome:
                raise ValueError("OCI volume return has started")
            if action == "attach" and not slot.volume_id:
                raise ValueError("Missing OCI volume creation receipt")
            if (action == "create" and slot.volume_id) or (
                action == "attach" and slot.attachment_id
            ):
                raise ValueError("OCI volume operation already has a receipt")
            if intent_key + "/refused" in row.provider_data:
                raise ValueError(
                    "OCI previously refused this volume operation; reconciliation is required"
                )
            now = self._clock()
            if intent_key in row.provider_data:
                saved = row.provider_data[intent_key]
                if not saved.get("token") or not 0 <= now - saved["started"] < _REPLAY_SECONDS:
                    raise ValueError(
                        "OCI volume ownership unresolved: retry window expired or invalid"
                    )
                return saved
            return {"token": str(uuid4()), "started": now}

        return self._write(request_id, intent_key, intent)["token"]

    def refuse(
        self, request_id: str, worker_id: str, index: int, action: Literal["create", "attach"]
    ) -> None:
        intent = f"{self.plan_key(worker_id)}/{index}/{action}"

        def verify(row: Request) -> bool:
            if action not in ("create", "attach") or intent not in row.provider_data:
                raise ValueError("Missing original OCI volume operation")
            return True

        self._write(request_id, intent + "/refused", verify)

    def _record(
        self, request_id: str, worker_id: str, index: int, kind: str, resource_id: str
    ) -> None:
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise ValueError("Missing OCI resource ID in service receipt")
        key = f"{self.plan_key(worker_id)}/{index}/{kind}"

        def receipt(row: Request) -> str:
            slot = self._slot(row, worker_id, index)
            if not (slot.create_started if kind == "volume" else slot.attach_started):
                raise ValueError("OCI receipt has no matching recorded operation")
            if any(
                k != key and k.startswith(_PREFIX) and k.endswith("/" + kind) and v == resource_id
                for k, v in row.provider_data.items()
            ):
                raise ValueError("OCI resource ID is already owned by a different volume slot")
            return resource_id

        self._write(request_id, key, receipt)

    def record_volume(self, request_id: str, worker_id: str, index: int, volume_id: str) -> None:
        self._record(request_id, worker_id, index, "volume", volume_id)

    def record_attachment(
        self, request_id: str, worker_id: str, index: int, attachment_id: str
    ) -> None:
        self._record(request_id, worker_id, index, "attachment", attachment_id)

    def confirm_attached(self, request_id: str, worker_id: str, index: int) -> None:
        def verify(row: Request) -> bool:
            slot = self._slot(row, worker_id, index)
            if not slot.attachment_id:
                raise ValueError("Cannot confirm an OCI attachment without a receipt")
            return True

        self._write(request_id, f"{self.plan_key(worker_id)}/{index}/attached", verify)

    def start_return(
        self, request_id: str, worker_id: str, *, unaccepted_only: bool = False
    ) -> None:
        def fence(row: Request) -> bool:
            if unaccepted_only and (
                self.plan_key(worker_id) + "/accepted" in row.provider_data
                or worker_id in row.machine_ids
            ):
                raise ValueError("Refusing to roll back an OCI worker already accepted by ORB")
            if not self._slots(row, worker_id):
                raise ValueError("No matching OCI volume plan for this worker")
            return True

        self._write(request_id, self.plan_key(worker_id) + "/return", fence)
        if self.on_change is not None:
            self.on_change(request_id, worker_id)

    def finish(
        self,
        request_id: str,
        worker_id: str,
        index: int,
        outcome: Literal["retained", "deleted", "skipped"],
    ) -> None:
        key = self.plan_key(worker_id)

        def verify(row: Request) -> str:
            slot = self._slot(row, worker_id, index)
            if not row.provider_data.get(key + "/return"):
                raise ValueError("OCI volume cleanup must first fence acquisition")
            expected = "deleted" if slot.spec["delete_on_return"] else "retained"
            if not slot.create_started:
                expected = "skipped"
            elif not slot.volume_id:
                raise ValueError("Missing OCI volume receipt for cleanup")
            if outcome != expected:
                raise ValueError("OCI volume disposition disagrees with saved policy")
            return outcome

        self._write(request_id, f"{key}/{index}/outcome", verify)

    def _workers(self, row: Request) -> list[str]:
        return [
            key[len(_PREFIX) : -len("/plan")]
            for key, value in row.provider_data.items()
            if key.startswith(_PREFIX) and key.endswith("/plan") and value["region"] == self._region
        ]

    def abandoned(self, request_id: str) -> bool:
        """Only explicit cancellation gives the broker permission to abort the worker."""
        with self._store() as uow:
            return self._read(uow, request_id).status == RequestStatus.CANCELLED

    def _recovery_state(self, row: Request, worker: str) -> Literal["recover", "waiting", "done"]:
        slots = self._slots(row, worker)
        if not slots or all(s.outcome for s in slots):
            return "done"
        if slots[0].returning or row.status == RequestStatus.CANCELLED:
            return "recover"
        if not row.status.is_active() or all(s.attached_confirmed for s in slots):
            return "done"
        if row.deadline_at and row.deadline_at.timestamp() <= self._clock():
            return "done"
        # A newly recorded plan may precede ORB's shared acquire checkpoint.
        return "recover" if worker in row.resource_ids else "waiting"

    def recovery_state(self, request_id: str, worker: str) -> Literal["recover", "waiting", "done"]:
        with self._store() as uow:
            return self._recovery_state(self._read(uow, request_id), worker)

    def pending_workers(self) -> list[tuple[str, str]]:
        """Discover interrupted work once at startup; subsequent polls use exact IDs."""
        with self._create_uow() as uow:
            return [
                (row.request_id.value, worker)
                for row in uow.requests.find_by_type(RequestType.ACQUIRE)
                if self._belongs(row)
                for worker in self._workers(row)
                if self._recovery_state(row, worker) == "recover"
            ]

    def origin_for_worker(self, worker_id: str) -> str | None:
        # Legacy workers may be read on backends unsuitable for new volume writes.
        # Gate the durable backend only once there is an actual journal to resume.
        with self._create_uow() as uow:
            machines = uow.machines.find_by_ids([worker_id])
            if len(machines) != 1 or not machines[0].request_id:
                return None
            row = uow.requests.get_by_id(RequestId(value=machines[0].request_id))
            if (
                row is None
                or not self._belongs(row)
                or self.plan_key(worker_id) not in row.provider_data
            ):
                return None
            self._slots(row, worker_id)  # verify the region and saved ownership records
            return row.request_id.value
