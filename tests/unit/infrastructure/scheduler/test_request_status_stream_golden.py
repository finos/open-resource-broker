"""The request status payload the SDK stream readers are tested against.

The SDKs parse this exact JSON in their unit tests. If the server's formatter
output changes, this test fails and the SDK fixtures must be regenerated.
"""

import json
from datetime import datetime, timezone

import pytest

from orb.application.request.dto import MachineReferenceDTO, RequestDTO
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

GOLDEN = """{"requests":[{"request_id":"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73","status":"complete","template_id":"t1","requested_count":2,"created_at":"2026-10-31T01:00:00Z","machine_ids":[],"message":"done","metadata":{},"request_type":"acquire","desired_capacity":1,"successful_count":2,"failed_count":0,"error_details":{},"provider_data":{},"version":0,"resource_ids":[],"machines":[{"machine_id":"i-0abc123def4567890","name":"ip-10-0-1-12","result":"succeed","status":"running","private_ip_address":"10.0.1.12","public_ip_address":"54.12.34.56","instance_type":"t3.micro","price_type":"ondemand","cloud_host_id":"ip-10-0-1-12.ec2.internal","launch_time":1793411323,"request_id":"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73","message":""},{"machine_id":"i-0def456abc7890123","name":"ip-10-0-1-13","result":"executing","status":"pending","request_id":"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73","message":"","launch_time":null,"cloud_host_id":null}]}],"message":"Request status retrieved successfully","count":1}"""


@pytest.mark.unit
def test_formatter_output_matches_the_golden_stream_payload():
    request_id = "req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73"
    running = MachineReferenceDTO(
        machine_id="i-0abc123def4567890",
        name="ip-10-0-1-12",
        result="succeed",
        status="running",
        private_ip_address="10.0.1.12",
        public_ip_address="54.12.34.56",
        instance_type="t3.micro",
        price_type="ondemand",
        cloud_host_id="ip-10-0-1-12.ec2.internal",
        launch_time=1793411323,
        request_id=request_id,
    )
    pending = MachineReferenceDTO(
        machine_id="i-0def456abc7890123",
        name="ip-10-0-1-13",
        result="executing",
        status="pending",
        request_id=request_id,
    )
    request = RequestDTO(
        request_id=request_id,
        status="complete",
        template_id="t1",
        requested_count=2,
        created_at=datetime(2026, 10, 31, 1, 0, 0, tzinfo=timezone.utc),
        machine_references=[running, pending],
        message="done",
        successful_count=2,
        failed_count=0,
    )

    scheduler = object.__new__(DefaultSchedulerStrategy)
    payload = DefaultSchedulerStrategy.format_request_status_response(scheduler, [request])

    assert json.loads(json.dumps(payload)) == json.loads(GOLDEN)
    machine = payload["requests"][0]["machines"][0]
    assert isinstance(machine["launch_time"], int)
    assert "private_ip_address" in machine and "public_ip_address" in machine
