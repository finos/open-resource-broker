"""Provider test scenarios for comprehensive testing."""

from typing import Any, Dict, List

from src.domain.base.value_objects import InstanceId


class ProviderScenarios:
    """Test scenarios for provider operations."""

    @staticmethod
    def get_success_scenarios() -> List[Dict[str, Any]]:
        """Get successful operation scenarios."""
        return [
            {
                "name": "all_instances_succeed",
                "description": "All instances created and running successfully",
                "create_instances": [
                    InstanceId(value="test-001"),
                    InstanceId(value="test-002"),
                ],
                "instance_status": {
                    InstanceId(value="test-001"): "running",
                    InstanceId(value="test-002"): "running",
                },
                "expected_request_status": "complete",
                "expected_machine_results": ["succeed", "succeed"],
            },
            {
                "name": "instances_pending_then_running",
                "description": "Instances start pending then become running",
                "create_instances": [
                    InstanceId(value="test-003"),
                    InstanceId(value="test-004"),
                ],
                "status_progression": [
                    {
                        InstanceId(value="test-003"): "pending",
                        InstanceId(value="test-004"): "pending",
                    },
                    {
                        InstanceId(value="test-003"): "running",
                        InstanceId(value="test-004"): "running",
                    },
                ],
                "expected_request_status": "complete",
                "expected_machine_results": ["succeed", "succeed"],
            },
        ]

    @staticmethod
    def get_failure_scenarios() -> List[Dict[str, Any]]:
        """Get failure operation scenarios."""
        return [
            {
                "name": "all_instances_fail",
                "description": "All instances fail to create",
                "create_instances": [],
                "create_error": "Provider capacity exceeded",
                "expected_request_status": "complete_with_error",
                "expected_machine_results": [],
            },
            {
                "name": "some_instances_fail",
                "description": "Some instances succeed, others fail",
                "create_instances": [InstanceId(value="test-005")],
                "instance_status": {
                    InstanceId(value="test-005"): "running",
                    InstanceId(value="test-006"): "terminated",  # Failed to start
                },
                "expected_request_status": "complete_with_error",
                "expected_machine_results": ["succeed", "fail"],
            },
        ]

    @staticmethod
    def get_status_transition_scenarios() -> List[Dict[str, Any]]:
        """Get status transition scenarios."""
        return [
            {
                "name": "pending_to_running",
                "machine_status": "pending",
                "machine_result": "executing",
                "request_status": "running",
            },
            {
                "name": "running_success",
                "machine_status": "running",
                "machine_result": "succeed",
                "request_status": "complete",
            },
            {
                "name": "terminated_failure",
                "machine_status": "terminated",
                "machine_result": "fail",
                "request_status": "complete_with_error",
            },
            {
                "name": "stopping_in_progress",
                "machine_status": "stopping",
                "machine_result": "executing",
                "request_status": "running",
            },
            {
                "name": "stopped_success",
                "machine_status": "stopped",
                "machine_result": "succeed",
                "request_status": "complete",
            },
            {
                "name": "shutting_down_in_progress",
                "machine_status": "shutting-down",
                "machine_result": "executing",
                "request_status": "running",
            },
        ]

    @staticmethod
    def get_return_request_scenarios() -> List[Dict[str, Any]]:
        """Get return request scenarios."""
        return [
            {
                "name": "successful_termination",
                "machines": [
                    {"name": "test-machine-1", "machineId": "test-001"},
                    {"name": "test-machine-2", "machineId": "test-002"},
                ],
                "termination_success": True,
                "expected_status": "complete",
                "expected_results": ["succeed", "succeed"],
            },
            {
                "name": "partial_termination_failure",
                "machines": [
                    {"name": "test-machine-3", "machineId": "test-003"},
                    {"name": "test-machine-4", "machineId": "test-004"},
                ],
                "termination_success": False,
                "expected_status": "complete_with_error",
                "expected_results": ["succeed", "fail"],
            },
        ]

    @staticmethod
    def get_template_scenarios() -> List[Dict[str, Any]]:
        """Get template validation scenarios."""
        return [
            {
                "name": "valid_template",
                "template": {
                    "templateId": "valid-template",
                    "maxNumber": 10,
                    "image_id": "ami-12345678",
                    "instance_type": "t2.micro",
                    "provider_api": "ec2_fleet",
                },
                "validation_result": {"valid": True, "errors": []},
            },
            {
                "name": "invalid_template_missing_fields",
                "template": {
                    "templateId": "invalid-template",
                    "maxNumber": 10,
                    # Missing required fields
                },
                "validation_result": {
                    "valid": False,
                    "errors": ["image_id is required", "instance_type is required"],
                },
            },
        ]

    @staticmethod
    def configure_mock_provider(mock_provider, scenario: Dict[str, Any]):
        """Configure mock provider with scenario data."""
        if "create_instances" in scenario:
            mock_provider.set_response("create_instances", scenario["create_instances"])

        if "instance_status" in scenario:
            mock_provider.set_response("get_instance_status", scenario["instance_status"])

        if "create_error" in scenario:
            mock_provider.set_response("create_instances", Exception(scenario["create_error"]))

        if "termination_success" in scenario:
            mock_provider.set_response("terminate_instances", scenario["termination_success"])

        if "validation_result" in scenario:
            mock_provider.set_response("validate_template", scenario["validation_result"])


# Host Factory format validators
class HostFactoryFormatValidator:
    """Validate responses match Host Factory specification."""

    @staticmethod
    def validate_templates_response(response: Dict[str, Any]) -> bool:
        """Validate getAvailableTemplates response format."""
        if "templates" not in response:
            return False

        for template in response["templates"]:
            required_fields = ["templateId", "maxNumber", "attributes"]
            if not all(field in template for field in required_fields):
                return False

            # Validate attributes structure
            attrs = template["attributes"]
            required_attrs = ["type", "ncpus", "nram"]
            if not all(attr in attrs for attr in required_attrs):
                return False

        return True

    @staticmethod
    def validate_request_response(response: Dict[str, Any]) -> bool:
        """Validate requestMachines response format."""
        required_fields = ["requestId", "message"]
        return all(field in response for field in required_fields)

    @staticmethod
    def validate_status_response(response: Dict[str, Any]) -> bool:
        """Validate getRequestStatus response format."""
        if "requests" not in response:
            return False

        for request in response["requests"]:
            required_fields = ["requestId", "status"]
            if not all(field in request for field in required_fields):
                return False

            # Validate status values
            valid_statuses = ["running", "complete", "complete_with_error"]
            if request["status"] not in valid_statuses:
                return False

            # Validate machines if present
            if "machines" in request:
                for machine in request["machines"]:
                    machine_fields = ["machineId", "name", "result", "status"]
                    if not all(field in machine for field in machine_fields):
                        return False

                    # Validate result values
                    valid_results = ["executing", "fail", "succeed"]
                    if machine["result"] not in valid_results:
                        return False

                    # Validate status values
                    valid_machine_statuses = [
                        "running",
                        "stopped",
                        "terminated",
                        "shutting-down",
                        "stopping",
                    ]
                    if machine["status"] not in valid_machine_statuses:
                        return False

        return True
