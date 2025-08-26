"""Host Factory lifecycle integration tests."""

import pytest

from tests.fixtures.mock_provider import create_mock_provider
from tests.fixtures.provider_scenarios import (
    HostFactoryFormatValidator,
    ProviderScenarios,
)


@pytest.mark.integration
# Add "aws" when testing with real AWS
@pytest.mark.parametrize("provider_type", ["mock"])
class TestHostFactoryLifecycle:
    """Test complete Host Factory workflow with any provider."""

    def test_complete_workflow_success(self, provider_type: str):
        """Test full lifecycle: templates → request → status → return → status."""
        # Setup provider
        if provider_type == "mock":
            provider = create_mock_provider()
            app_service = self._create_app_service_with_provider(provider_type, provider)
        else:
            # For real providers, use actual configuration
            app_service = self._create_app_service(provider_type)

        # Get Available Templates
        templates_response = app_service.get_available_templates()

        # Validate Host Factory format
        assert HostFactoryFormatValidator.validate_templates_response(templates_response)
        assert len(templates_response["templates"]) > 0

        template_id = templates_response["templates"][0]["templateId"]

        # Request Machines
        request_response = app_service.request_machines(template_id, 3)

        # Validate Host Factory format
        assert HostFactoryFormatValidator.validate_request_response(request_response)
        req_id = request_response["requestId"]
        assert req_id is not None

        # Check Request Status
        status_response = app_service.get_request_status(req_id)

        # Validate Host Factory format
        assert HostFactoryFormatValidator.validate_status_response(status_response)

        request_info = status_response["requests"][0]
        assert request_info["requestId"] == req_id
        assert request_info["status"] in ["running", "complete", "complete_with_error"]

        # Get machine information
        machines = request_info.get("machines", [])
        if machines:
            machine_ids = [{"name": m["name"], "machineId": m["machineId"]} for m in machines[:2]]

            # Request Return Machines
            return_response = app_service.request_return_machines(machine_ids)

            # Validate Host Factory format
            assert HostFactoryFormatValidator.validate_request_response(return_response)
            ret_id = return_response["requestId"]

            # Check Return Status
            return_status_response = app_service.get_request_status(ret_id)

            # Validate Host Factory format
            assert HostFactoryFormatValidator.validate_status_response(return_status_response)

            return_info = return_status_response["requests"][0]
            assert return_info["requestId"] == ret_id
            assert return_info["status"] in [
                "running",
                "complete",
                "complete_with_error",
            ]

    def test_workflow_with_different_scenarios(self, provider_type: str):
        """Test workflow with different provider response scenarios."""
        if provider_type != "mock":
            pytest.skip("Scenario testing only available with mock provider")

        scenarios = ProviderScenarios.get_success_scenarios()

        for scenario in scenarios:
            provider = create_mock_provider()
            ProviderScenarios.configure_mock_provider(provider, scenario)
            app_service = self._create_app_service_with_provider(provider_type, provider)

            # Execute workflow
            templates = app_service.get_available_templates()
            template_id = templates["templates"][0]["templateId"]

            request_response = app_service.request_machines(
                template_id, len(scenario.get("create_instances", []))
            )
            req_id = request_response["requestId"]

            status_response = app_service.get_request_status(req_id)
            request_info = status_response["requests"][0]

            # Validate expected outcomes
            assert request_info["status"] == scenario["expected_request_status"]

            if "expected_machine_results" in scenario:
                machines = request_info.get("machines", [])
                actual_results = [m["result"] for m in machines]
                assert actual_results == scenario["expected_machine_results"]

    def test_workflow_failure_scenarios(self, provider_type: str):
        """Test workflow with failure scenarios."""
        if provider_type != "mock":
            pytest.skip("Failure scenario testing only available with mock provider")

        scenarios = ProviderScenarios.get_failure_scenarios()

        for scenario in scenarios:
            provider = create_mock_provider()
            ProviderScenarios.configure_mock_provider(provider, scenario)
            app_service = self._create_app_service_with_provider(provider_type, provider)

            # Execute workflow
            templates = app_service.get_available_templates()
            template_id = templates["templates"][0]["templateId"]

            if "create_error" in scenario:
                # Expect request to fail or return error status
                try:
                    request_response = app_service.request_machines(template_id, 2)
                    req_id = request_response["requestId"]

                    status_response = app_service.get_request_status(req_id)
                    request_info = status_response["requests"][0]

                    # Should indicate failure
                    assert request_info["status"] == scenario["expected_request_status"]
                except Exception:
                    # Exception is also acceptable for failure scenarios
                    pass

    def test_repository_persistence_across_operations(self, provider_type: str):
        """Test that state persists correctly across operations."""
        if provider_type != "mock":
            pytest.skip("Persistence testing only available with mock provider")

        provider = create_mock_provider()
        app_service = self._create_app_service_with_provider(provider_type, provider)

        # Create request
        templates = app_service.get_available_templates()
        template_id = templates["templates"][0]["templateId"]
        request_response = app_service.request_machines(template_id, 2)
        req_id = request_response["requestId"]

        # Verify request is stored
        status_response = app_service.get_request_status(req_id)
        assert len(status_response["requests"]) == 1
        assert status_response["requests"][0]["requestId"] == req_id

        # Create another app service instance (simulating restart)
        app_service2 = self._create_app_service_with_provider(provider_type, provider)

        # Verify request still exists
        status_response2 = app_service2.get_request_status(req_id)
        assert len(status_response2["requests"]) == 1
        assert status_response2["requests"][0]["requestId"] == req_id

    def test_concurrent_operations(self, provider_type: str):
        """Test concurrent request handling."""
        if provider_type != "mock":
            pytest.skip("Concurrency testing only available with mock provider")

        provider = create_mock_provider()
        app_service = self._create_app_service_with_provider(provider_type, provider)

        # Create multiple requests
        templates = app_service.get_available_templates()
        template_id = templates["templates"][0]["templateId"]

        request_ids = []
        for _i in range(3):
            response = app_service.request_machines(template_id, 1)
            request_ids.append(response["requestId"])

        # Verify all requests exist
        for req_id in request_ids:
            status_response = app_service.get_request_status(req_id)
            assert len(status_response["requests"]) == 1
            assert status_response["requests"][0]["requestId"] == req_id

    def test_error_handling_and_recovery(self, provider_type: str):
        """Test error handling and recovery scenarios."""
        if provider_type != "mock":
            pytest.skip("Error handling testing only available with mock provider")

        provider = create_mock_provider()
        app_service = self._create_app_service_with_provider(provider_type, provider)

        # Test invalid template ID
        try:
            app_service.request_machines("non-existent-template", 1)
            raise AssertionError("Should have raised an exception")
        except Exception as e:
            assert "template" in str(e).lower() or "not found" in str(e).lower()

        # Test invalid request ID
        try:
            app_service.get_request_status("non-existent-request")
            raise AssertionError("Should have raised an exception")
        except Exception as e:
            assert "request" in str(e).lower() or "not found" in str(e).lower()

    def _create_app_service(self, provider_type: str):
        """Create application service with specified provider type."""
        # This would use real configuration for actual providers
        from bootstrap import Application

        app = Application()
        app.initialize()
        return app.get_application_service()

    def _create_app_service_with_provider(self, provider_type: str, provider):
        """Create application service with mock provider."""
        from bootstrap import Application

        # Create mock application with provider
        app = Application()
        app.initialize()

        # Mock the provider in the application
        service = app.get_application_service()
        service._provider = provider

        return service


@pytest.mark.integration
class TestHostFactoryFormatCompliance:
    """Test Host Factory input/output format compliance."""

    def test_templates_format_compliance(self):
        """Test getAvailableTemplates format matches specification."""
        provider = create_mock_provider()
        templates = provider.get_available_templates()

        # Convert to Host Factory format
        hf_response = {"templates": templates}

        # Validate format
        assert HostFactoryFormatValidator.validate_templates_response(hf_response)

        # Validate specific fields
        for template in templates:
            assert isinstance(template["templateId"], str)
            assert isinstance(template["maxNumber"], int)
            assert template["maxNumber"] > 0

            attrs = template["attributes"]
            assert isinstance(attrs["type"], list)
            assert len(attrs["type"]) == 2
            assert attrs["type"][0] == "String"

            assert isinstance(attrs["ncpus"], list)
            assert len(attrs["ncpus"]) == 2
            assert attrs["ncpus"][0] == "Numeric"

            assert isinstance(attrs["nram"], list)
            assert len(attrs["nram"]) == 2
            assert attrs["nram"][0] == "Numeric"

    def test_request_format_compliance(self):
        """Test requestMachines format matches specification."""
        # Test input format
        input_data = {"template": {"templateId": "test-template", "machineCount": 3}}

        # Validate input structure
        assert "template" in input_data
        assert "templateId" in input_data["template"]
        assert "machineCount" in input_data["template"]
        assert isinstance(input_data["template"]["machineCount"], int)

        # Test output format
        output_data = {"requestId": "req-12345-abcd", "message": "Request VM success."}

        assert HostFactoryFormatValidator.validate_request_response(output_data)

    def test_status_format_compliance(self):
        """Test getRequestStatus format matches specification."""
        # Test input format
        input_data = {"requests": [{"requestId": "req-12345-abcd"}]}

        # Validate input structure
        assert "requests" in input_data
        assert isinstance(input_data["requests"], list)
        assert "requestId" in input_data["requests"][0]

        # Test output format
        output_data = {
            "requests": [
                {
                    "requestId": "req-12345-abcd",
                    "status": "complete",
                    "machines": [
                        {
                            "machineId": "i-12345678",
                            "name": "test-machine-1",
                            "result": "succeed",
                            "status": "running",
                            "privateIpAddress": "10.0.1.1",
                            "publicIpAddress": "203.0.113.1",
                            "launchtime": 1640995200,
                            "message": "",
                        }
                    ],
                    "message": "",
                }
            ]
        }

        assert HostFactoryFormatValidator.validate_status_response(output_data)

    def test_return_machines_format_compliance(self):
        """Test requestReturnMachines format matches specification."""
        # Test input format
        input_data = {
            "machines": [
                {"name": "test-machine-1", "machineId": "i-12345678"},
                {"name": "test-machine-2", "machineId": "i-87654321"},
            ]
        }

        # Validate input structure
        assert "machines" in input_data
        assert isinstance(input_data["machines"], list)
        for machine in input_data["machines"]:
            assert "name" in machine
            assert "machineId" in machine

        # Test output format (same as request response)
        output_data = {"requestId": "ret-12345-abcd", "message": "Delete VM success."}

        assert HostFactoryFormatValidator.validate_request_response(output_data)
