"""Unit tests for SlurmResponseFormatter.

Covers open-resource-broker-2706.4's coverage goal for the response
formatting paths not exercised via the strategy-level contract tests.
"""

from orb.infrastructure.scheduler.slurm.response_formatter import SlurmResponseFormatter


class _ToDictObj:
    def __init__(self, **data):
        self._data = data

    def to_dict(self):
        return self._data


class _ModelDumpObj:
    def __init__(self, **data):
        self._data = data

    def model_dump(self):
        return self._data


def test_to_dict_passthrough_for_plain_dict():
    assert SlurmResponseFormatter._to_dict({"a": 1}) == {"a": 1}


def test_to_dict_uses_model_dump_when_available():
    obj = _ModelDumpObj(request_id="req-1")
    assert SlurmResponseFormatter._to_dict(obj) == {"request_id": "req-1"}


def test_to_dict_uses_to_dict_when_available():
    obj = _ToDictObj(request_id="req-1")
    assert SlurmResponseFormatter._to_dict(obj) == {"request_id": "req-1"}


def test_to_dict_returns_empty_for_unsupported_object():
    assert SlurmResponseFormatter._to_dict(object()) == {}


def test_format_request_status_response_envelope():
    formatter = SlurmResponseFormatter()
    result = formatter.format_request_status_response(
        [{"request_id": "req-1", "status": "complete", "machines": [], "message": "done"}]
    )

    assert result["count"] == 1
    assert result["message"] == "Request status retrieved successfully"
    assert result["requests"][0]["request_id"] == "req-1"


def test_format_request_status_response_accepts_dto_like_objects():
    formatter = SlurmResponseFormatter()
    obj = _ToDictObj(request_id="req-2", status="pending", machines=[], message="")

    result = formatter.format_request_status_response([obj])

    assert result["requests"][0]["request_id"] == "req-2"


def test_format_return_requests_response_shape():
    formatter = SlurmResponseFormatter()
    result = formatter.format_return_requests_response(
        [
            {
                "request_id": "ret-1",
                "status": "complete",
                "message": "done",
                "grace_period": 60,
                "machines": [{"machine_id": "i-abc", "name": "compute-001"}],
            }
        ]
    )

    assert result["return_requests"][0]["request_id"] == "ret-1"
    assert result["return_requests"][0]["machines"] == [
        {"machine_id": "i-abc", "node_name": "compute-001"}
    ]


def test_format_return_requests_response_falls_back_to_machine_references():
    formatter = SlurmResponseFormatter()
    result = formatter.format_return_requests_response(
        [
            {
                "request_id": "ret-2",
                "status": "complete",
                "message": "done",
                "grace_period": None,
                "machine_references": [{"machine_id": "i-def", "name": "compute-002"}],
            }
        ]
    )

    assert result["return_requests"][0]["machines"] == [
        {"machine_id": "i-def", "node_name": "compute-002"}
    ]


def test_format_return_requests_response_empty_machines():
    formatter = SlurmResponseFormatter()
    result = formatter.format_return_requests_response(
        [{"request_id": "ret-3", "status": "pending", "message": "", "grace_period": 30}]
    )

    assert result["return_requests"][0]["machines"] == []
