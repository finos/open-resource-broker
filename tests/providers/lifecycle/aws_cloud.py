"""AWS ground truth: moto holds the instances, this layer steers and records.

Every botocore call is wrapped. Mutating calls are logged with the machines they
killed (expanded from the state at that moment); describe responses are rendered
through the state the scenario set (pending, shutting-down). Instance data
itself (ids, addresses, images) is whatever moto produced.
"""

from __future__ import annotations

from typing import Any, Optional

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from tests.providers.lifecycle.cloud import (
    PENDING,
    RUNNING,
    STOPPING,
    TERMINATED,
    CallRecord,
    Cloud,
    CloudMachine,
)

_STATE_CODES = {PENDING: 0, RUNNING: 16, STOPPING: 32, TERMINATED: 48}
_MUTATING = frozenset(
    {
        "RunInstances",
        "CreateFleet",
        "TerminateInstances",
        "DeleteFleets",
        "ModifyFleet",
        "CancelSpotFleetRequests",
        "ModifySpotFleetRequest",
        "CreateAutoScalingGroup",
        "DeleteAutoScalingGroup",
        "UpdateAutoScalingGroup",
    }
)


def _client_error(code: str, message: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


class AwsCloud(Cloud):
    def __init__(self, region: str) -> None:
        self.region = region
        self.call_log: list[CallRecord] = []
        self._held: set[str] = set()
        self._hold_budget = 0
        self._lag = False
        self._stopping: set[str] = set()
        self._capacity: Optional[int] = None
        self._fail_abort = False
        self._fleet_targets: dict[str, int] = {}
        self._fail_reads = False
        self._interrupted: set[str] = set()
        self._orig: Any = None
        self._truth_client: Any = None
        self._in_truth = False

    # ---- installation -------------------------------------------------------------

    def install(self, monkeypatch: Any) -> None:
        orig = BaseClient._make_api_call
        self._orig = orig
        cloud = self

        def wrapped(client: Any, operation_name: str, api_params: dict[str, Any]) -> Any:
            if cloud._in_truth:
                return orig(client, operation_name, api_params)
            return cloud._call(orig, client, operation_name, api_params)

        monkeypatch.setattr(BaseClient, "_make_api_call", wrapped)

    def _ec2(self) -> Any:
        if self._truth_client is None:
            self._truth_client = boto3.client("ec2", region_name=self.region)
        return self._truth_client

    def _truth(self, operation: str, **params: Any) -> Any:
        self._in_truth = True
        try:
            return getattr(self._ec2(), _snake(operation))(**params)
        finally:
            self._in_truth = False

    # ---- interception -------------------------------------------------------------

    def _call(self, orig: Any, client: Any, op: str, params: dict[str, Any]) -> Any:
        service = client.meta.service_model.service_name
        record: Optional[CallRecord] = None
        if op in _MUTATING:
            record = CallRecord(seq=len(self.call_log), operation=op, params=dict(params))
            self.call_log.append(record)
        if self._fail_reads and op.startswith("Describe"):
            raise _client_error("InternalError", "An internal error has occurred", op)
        if op == "RunInstances":
            params = self._limit_run_instances(dict(params), record)
        if op == "CreateFleet":
            return self._create_fleet(orig, client, op, params, record)
        if op == "TerminateInstances":
            return self._terminate(orig, client, op, params, record)
        if op == "DeleteFleets":
            return self._delete_fleets(orig, client, op, params, record)
        if op == "ModifyFleet":
            raise _client_error(
                "InvalidParameterValue",
                "ModifyFleet is only supported for fleets of type 'maintain'",
                op,
            )
        if op == "DescribeInstances" and any(
            f.get("Name") == "reservation-id" for f in params.get("Filters") or []
        ):
            return self._describe_by_reservation(orig, client, op, params, record)
        response = orig(client, op, params)
        if service == "ec2":
            self._after_ec2(op, response, record)
            if op == "DescribeFleets":
                self._restore_fleet_targets(response)
        return response

    def _restore_fleet_targets(self, response: Any) -> None:
        for fleet in response.get("Fleets", []):
            wanted = self._fleet_targets.get(fleet.get("FleetId"))
            if wanted is not None:
                fleet.setdefault("TargetCapacitySpecification", {})["TotalTargetCapacity"] = wanted

    def _describe_by_reservation(
        self, orig: Any, client: Any, op: str, params: dict, record: Any
    ) -> Any:
        """Answer the ``reservation-id`` filter moto does not implement."""
        filters = params.get("Filters") or []
        wanted = {v for f in filters if f.get("Name") == "reservation-id" for v in f["Values"]}
        rest = [f for f in filters if f.get("Name") != "reservation-id"]
        narrowed = {k: v for k, v in params.items() if k != "Filters"}
        if rest:
            narrowed["Filters"] = rest
        response = orig(client, op, narrowed)
        response["Reservations"] = [
            r for r in response.get("Reservations", []) if r.get("ReservationId") in wanted
        ]
        self._after_ec2(op, response, record)
        return response

    def _limit_run_instances(self, params: dict[str, Any], record: Optional[CallRecord]) -> dict:
        """Launch as many as the scenario's capacity allows, up to MaxCount.

        Real RunInstances launches between MinCount and MaxCount. moto launches
        MinCount, so the call is rewritten to ask moto for exactly what the
        cloud would have launched.
        """
        wanted = int(params.get("MaxCount", 1))
        minimum = int(params.get("MinCount", 1))
        granted = wanted if self._capacity is None else min(wanted, self._capacity)
        if granted < minimum or granted <= 0:
            raise _client_error(
                "InsufficientInstanceCapacity",
                "We currently do not have sufficient capacity in the requested zone.",
                "RunInstances",
            )
        if self._capacity is not None:
            self._capacity -= granted
        params["MaxCount"] = granted
        params["MinCount"] = granted
        return params

    def _create_fleet(self, orig: Any, client: Any, op: str, params: dict, record: Any) -> Any:
        """An instant fleet launches what capacity allows and reports the rest as errors."""
        spec = dict(params.get("TargetCapacitySpecification", {}))
        wanted = int(spec.get("TotalTargetCapacity", 1))
        granted = wanted if self._capacity is None else min(wanted, self._capacity)
        if self._capacity is not None:
            self._capacity -= granted
        errors: list[dict[str, Any]] = []
        if granted < wanted:
            errors = [
                {
                    "LaunchTemplateAndOverrides": {
                        "LaunchTemplateSpecification": params["LaunchTemplateConfigs"][0][
                            "LaunchTemplateSpecification"
                        ],
                        "Overrides": params["LaunchTemplateConfigs"][0]["Overrides"][0],
                    },
                    "Lifecycle": "on-demand",
                    "ErrorCode": "InsufficientInstanceCapacity",
                    "ErrorMessage": "We currently do not have sufficient capacity.",
                }
            ]
        if granted <= 0:
            response = {
                "FleetId": "fleet-00000000-0000-0000-0000-000000000000",
                "Errors": errors,
                "Instances": [],
                "ResponseMetadata": {"HTTPStatusCode": 200},
            }
            return response
        spec["TotalTargetCapacity"] = granted
        params = dict(params, TargetCapacitySpecification=spec)
        response = orig(client, op, params)
        if response.get("FleetId"):
            # moto was asked for what could be granted; the fleet itself keeps the
            # target that was requested, as it does in EC2.
            self._fleet_targets[response["FleetId"]] = wanted
        if errors:
            response["Errors"] = list(response.get("Errors", [])) + errors
        self._after_ec2(op, response, record)
        return response

    def _after_ec2(self, op: str, response: Any, record: Optional[CallRecord]) -> None:
        if op == "RunInstances":
            ids = [i["InstanceId"] for i in response.get("Instances", [])]
            if record is not None:
                record.created = ids
            self._hold(ids)
            self._render(response.get("Instances", []))
        elif op == "CreateFleet":
            ids = [
                iid
                for entry in response.get("Instances", [])
                for iid in entry.get("InstanceIds", [])
            ]
            if record is not None:
                record.created = ids
                record.resource = (response.get("FleetId") or "") or None
            self._hold(ids)
        elif op == "DescribeInstances":
            for reservation in response.get("Reservations", []):
                self._render(reservation.get("Instances", []))

    def _hold(self, ids: list[str]) -> None:
        take = ids[: self._hold_budget]
        self._held |= set(take)
        self._hold_budget -= len(take)

    def _render(self, instances: list[dict[str, Any]]) -> None:
        for inst in instances:
            state = self._state_of(inst["InstanceId"], inst["State"]["Name"])
            inst["State"] = {"Code": _STATE_CODES[state], "Name": state}
            if inst["InstanceId"] in self._interrupted:
                inst["StateReason"] = {
                    "Code": "Server.SpotInstanceTermination",
                    "Message": "Server.SpotInstanceTermination: spot capacity no longer available",
                }

    def _state_of(self, instance_id: str, moto_state: str) -> str:
        if instance_id in self._stopping and moto_state != TERMINATED:
            return STOPPING
        if instance_id in self._held and moto_state == RUNNING:
            return PENDING
        return moto_state

    # ---- mutating calls -----------------------------------------------------------

    def _terminate(self, orig: Any, client: Any, op: str, params: dict, record: Any) -> Any:
        if self._fail_abort:
            raise _client_error("UnauthorizedOperation", "not permitted", op)
        ids = list(params.get("InstanceIds", []))
        live = {m.machine_id for m in self.machines() if m.state in (PENDING, RUNNING)}
        if record is not None:
            record.killed = {i for i in ids if i in live}
        if self._lag:
            self._stopping |= {i for i in ids if i in live}
            return {
                "TerminatingInstances": [
                    {
                        "InstanceId": i,
                        "CurrentState": {"Code": 32, "Name": STOPPING},
                        "PreviousState": {"Code": 16, "Name": RUNNING},
                    }
                    for i in ids
                ],
                "ResponseMetadata": {"HTTPStatusCode": 200},
            }
        self._held -= set(ids)
        return orig(client, op, params)

    def fleet_members(self, fleet_id: str) -> list[str]:
        resp = self._truth("DescribeFleetInstances", FleetId=fleet_id)
        return [i["InstanceId"] for i in resp.get("ActiveInstances", [])]

    def _delete_fleets(self, orig: Any, client: Any, op: str, params: dict, record: Any) -> Any:
        fleet_ids = list(params.get("FleetIds", []))
        terminate = bool(params.get("TerminateInstances"))
        if self._fail_abort:
            return {
                "SuccessfulFleetDeletions": [],
                "UnsuccessfulFleetDeletions": [
                    {
                        "FleetId": f,
                        "Error": {"Code": "UnauthorizedOperation", "Message": "not permitted"},
                    }
                    for f in fleet_ids
                ],
                "ResponseMetadata": {"HTTPStatusCode": 200},
            }
        members: list[str] = []
        for fleet_id in fleet_ids:
            try:
                members += self.fleet_members(fleet_id)
            except ClientError:
                continue
        live = {m.machine_id for m in self.machines() if m.state in (PENDING, RUNNING)}
        if record is not None:
            record.resource = ",".join(fleet_ids)
            record.killed = {m for m in members if m in live} if terminate else set()
        # The cloud, not moto, decides when members die: delete the fleet record only.
        response = orig(client, op, dict(params, TerminateInstances=False))
        if terminate and record is not None:
            doomed = sorted(record.killed)
            if self._lag:
                self._stopping |= set(doomed)
            elif doomed:
                self._held -= set(doomed)
                self._truth("TerminateInstances", InstanceIds=doomed)
        return response

    # ---- Cloud API ----------------------------------------------------------------

    def machines(self, request_id: Optional[str] = None) -> list[CloudMachine]:
        resp = self._truth("DescribeInstances")
        out: list[CloudMachine] = []
        for reservation in resp.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                owner = tags.get("RequestId")
                if request_id is not None and owner != request_id:
                    continue
                out.append(
                    CloudMachine(
                        machine_id=inst["InstanceId"],
                        state=self._state_of(inst["InstanceId"], inst["State"]["Name"]),
                        request_id=owner,
                        private_ip=inst.get("PrivateIpAddress"),
                        image_id=inst.get("ImageId"),
                    )
                )
        return out

    def advance(self) -> None:
        self._hold_budget = 0
        self._held.clear()
        doomed = sorted(self._stopping)
        self._stopping.clear()
        self._lag = False
        if doomed:
            self._truth("TerminateInstances", InstanceIds=doomed)

    def hold_pending(self, count: Optional[int] = None) -> None:
        self._hold_budget = 10**6 if count is None else count

    def release_pending(self) -> None:
        """Let held machines run while still lagging terminations."""
        self._hold_budget = 0
        self._held.clear()

    def lag_termination(self) -> None:
        self._lag = True

    def limit_capacity(self, machines: int) -> None:
        self._capacity = machines

    def interrupt(self, machine_ids: list[str]) -> None:
        self._interrupted |= set(machine_ids)
        self._held -= set(machine_ids)
        self._truth("TerminateInstances", InstanceIds=machine_ids)

    def fail_aborts(self, failing: bool = True) -> None:
        self._fail_abort = failing

    def fail_reads(self, failing: bool = True) -> None:
        self._fail_reads = failing

    def unknown_machine_id(self) -> str:
        return "i-0123456789abcdef0"


def _snake(operation: str) -> str:
    out = []
    for i, ch in enumerate(operation):
        if ch.isupper() and i:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)
