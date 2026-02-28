"""Tests for the clean in-process HostFactory mock."""

import pytest

from tests.mocks.hf_mock import HostFactoryMock


class TestHostFactoryMockInit:
    def test_default_scheduler_is_hostfactory(self):
        mock = HostFactoryMock()
        assert mock.scheduler == "hostfactory"

    def test_explicit_hostfactory_scheduler(self):
        mock = HostFactoryMock(scheduler="hostfactory")
        assert mock.scheduler == "hostfactory"

    def test_explicit_default_scheduler(self):
        mock = HostFactoryMock(scheduler="default")
        assert mock.scheduler == "default"

    def test_unknown_scheduler_raises(self):
        with pytest.raises(ValueError, match="Unknown scheduler type"):
            HostFactoryMock(scheduler="unknown")


class TestGetAvailableTemplates:
    def test_empty_when_no_templates_registered(self):
        mock = HostFactoryMock()
        res = mock.get_available_templates()
        assert res == {"templates": []}

    def test_returns_registered_templates(self):
        mock = HostFactoryMock()
        mock.add_template("tmpl-1", {"templateId": "tmpl-1", "providerApi": "ASG"})
        mock.add_template("tmpl-2", {"templateId": "tmpl-2", "providerApi": "EC2Fleet"})

        res = mock.get_available_templates()
        assert len(res["templates"]) == 2
        ids = {t["templateId"] for t in res["templates"]}
        assert ids == {"tmpl-1", "tmpl-2"}

    def test_template_data_is_preserved(self):
        mock = HostFactoryMock()
        mock.add_template(
            "tmpl-1", {"templateId": "tmpl-1", "providerApi": "ASG", "region": "us-east-1"}
        )

        res = mock.get_available_templates()
        tmpl = res["templates"][0]
        assert tmpl["region"] == "us-east-1"


class TestRequestMachinesHostfactory:
    def setup_method(self):
        self.mock = HostFactoryMock(scheduler="hostfactory")
        self.mock.add_template("tmpl-1", {"templateId": "tmpl-1"})

    def test_returns_camelcase_request_id(self):
        res = self.mock.request_machines("tmpl-1", 2)
        assert "requestId" in res
        assert res["requestId"].startswith("req-")

    def test_returns_success_message(self):
        res = self.mock.request_machines("tmpl-1", 2)
        assert "message" in res

    def test_request_starts_in_executing_status(self):
        res = self.mock.request_machines("tmpl-1", 2)
        request_id = res["requestId"]
        status = self.mock.get_request_status(request_id)
        assert status["requests"][0]["status"] == "executing"

    def test_executing_machines_have_correct_count(self):
        res = self.mock.request_machines("tmpl-1", 3)
        request_id = res["requestId"]
        status = self.mock.get_request_status(request_id)
        assert len(status["requests"][0]["machines"]) == 3


class TestRequestMachinesDefault:
    def setup_method(self):
        self.mock = HostFactoryMock(scheduler="default")
        self.mock.add_template("tmpl-1", {"template_id": "tmpl-1"})

    def test_returns_snake_case_request_id(self):
        res = self.mock.request_machines("tmpl-1", 1)
        assert "request_id" in res
        assert "requestId" not in res

    def test_request_starts_in_executing_status(self):
        res = self.mock.request_machines("tmpl-1", 1)
        request_id = res["request_id"]
        status = self.mock.get_request_status(request_id)
        assert status["requests"][0]["status"] == "executing"


class TestCompleteRequest:
    def setup_method(self):
        self.mock = HostFactoryMock(scheduler="hostfactory")
        self.mock.add_template("tmpl-1", {"templateId": "tmpl-1"})

    def test_complete_transitions_to_complete_status(self):
        res = self.mock.request_machines("tmpl-1", 2)
        request_id = res["requestId"]

        self.mock.complete_request(request_id)

        status = self.mock.get_request_status(request_id)
        assert status["requests"][0]["status"] == "complete"

    def test_complete_generates_machine_ids_when_not_provided(self):
        res = self.mock.request_machines("tmpl-1", 2)
        request_id = res["requestId"]

        self.mock.complete_request(request_id)

        status = self.mock.get_request_status(request_id)
        machines = status["requests"][0]["machines"]
        assert len(machines) == 2
        for m in machines:
            assert m["machineId"].startswith("i-")
            assert m["status"] == "running"

    def test_complete_uses_provided_machine_ids(self):
        res = self.mock.request_machines("tmpl-1", 2)
        request_id = res["requestId"]

        self.mock.complete_request(request_id, machine_ids=["i-aaa", "i-bbb"])

        status = self.mock.get_request_status(request_id)
        machines = status["requests"][0]["machines"]
        assert [m["machineId"] for m in machines] == ["i-aaa", "i-bbb"]

    def test_complete_unknown_request_raises(self):
        with pytest.raises(KeyError):
            self.mock.complete_request("req-doesnotexist")


class TestFailRequest:
    def setup_method(self):
        self.mock = HostFactoryMock(scheduler="hostfactory")
        self.mock.add_template("tmpl-1", {"templateId": "tmpl-1"})

    def test_fail_transitions_to_failed_status(self):
        res = self.mock.request_machines("tmpl-1", 1)
        request_id = res["requestId"]

        self.mock.fail_request(request_id)

        status = self.mock.get_request_status(request_id)
        assert status["requests"][0]["status"] == "failed"

    def test_fail_includes_message(self):
        res = self.mock.request_machines("tmpl-1", 1)
        request_id = res["requestId"]

        self.mock.fail_request(request_id, message="Capacity unavailable")

        status = self.mock.get_request_status(request_id)
        assert status["requests"][0]["message"] == "Capacity unavailable"

    def test_fail_unknown_request_raises(self):
        with pytest.raises(KeyError):
            self.mock.fail_request("req-doesnotexist")


class TestGetRequestStatusUnknown:
    def test_unknown_request_id_returns_empty_list(self):
        mock = HostFactoryMock()
        res = mock.get_request_status("req-doesnotexist")
        assert res == {"requests": []}


class TestRequestReturnMachines:
    def test_hostfactory_returns_camelcase_request_id(self):
        mock = HostFactoryMock(scheduler="hostfactory")
        res = mock.request_return_machines(["i-aaa", "i-bbb"])
        assert "requestId" in res
        assert res["requestId"].startswith("ret-")

    def test_default_returns_snake_case_request_id(self):
        mock = HostFactoryMock(scheduler="default")
        res = mock.request_return_machines(["i-aaa"])
        assert "request_id" in res
        assert "requestId" not in res

    def test_return_request_is_queryable(self):
        mock = HostFactoryMock(scheduler="hostfactory")
        res = mock.request_return_machines(["i-aaa", "i-bbb"])
        return_id = res["requestId"]

        status = mock.get_return_requests([return_id])
        assert len(status["requests"]) == 1
        assert status["requests"][0]["requestId"] == return_id
        assert status["requests"][0]["status"] == "executing"


class TestGetReturnRequests:
    def test_unknown_id_returns_unknown_status(self):
        mock = HostFactoryMock(scheduler="hostfactory")
        res = mock.get_return_requests(["ret-doesnotexist"])
        assert res["requests"][0]["status"] == "unknown"

    def test_multiple_ids_returned_in_order(self):
        mock = HostFactoryMock(scheduler="hostfactory")
        r1 = mock.request_return_machines(["i-aaa"])["requestId"]
        r2 = mock.request_return_machines(["i-bbb"])["requestId"]

        res = mock.get_return_requests([r1, r2])
        assert len(res["requests"]) == 2
        assert res["requests"][0]["requestId"] == r1
        assert res["requests"][1]["requestId"] == r2


class TestFullLifecycle:
    """End-to-end lifecycle: request -> complete -> return."""

    def test_hostfactory_full_lifecycle(self):
        mock = HostFactoryMock(scheduler="hostfactory")
        mock.add_template("tmpl-asg", {"templateId": "tmpl-asg", "providerApi": "ASG"})

        # 1. get templates
        templates = mock.get_available_templates()
        assert len(templates["templates"]) == 1

        # 2. request machines
        req_res = mock.request_machines("tmpl-asg", 2)
        request_id = req_res["requestId"]

        # 3. status is executing
        status = mock.get_request_status(request_id)
        assert status["requests"][0]["status"] == "executing"

        # 4. complete the request
        mock.complete_request(request_id, machine_ids=["i-001", "i-002"])

        # 5. status is now complete
        status = mock.get_request_status(request_id)
        assert status["requests"][0]["status"] == "complete"
        machines = status["requests"][0]["machines"]
        assert len(machines) == 2

        # 6. return the machines
        machine_ids = [m["machineId"] for m in machines]
        ret_res = mock.request_return_machines(machine_ids)
        return_id = ret_res["requestId"]

        # 7. return request is queryable
        ret_status = mock.get_return_requests([return_id])
        assert ret_status["requests"][0]["status"] == "executing"

    def test_default_scheduler_full_lifecycle(self):
        mock = HostFactoryMock(scheduler="default")
        mock.add_template("tmpl-fleet", {"template_id": "tmpl-fleet", "provider_api": "EC2Fleet"})

        req_res = mock.request_machines("tmpl-fleet", 1)
        request_id = req_res["request_id"]

        mock.complete_request(request_id)

        status = mock.get_request_status(request_id)
        assert status["requests"][0]["status"] == "complete"
        machines = status["requests"][0]["machines"]
        assert len(machines) == 1
        assert "machine_id" in machines[0]
        assert "machineId" not in machines[0]
