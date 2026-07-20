"""Unit tests for orb.cli.factories.utility_command_factory.

Covers all data-structure classes and the UtilityCommandFactory methods.
"""

from __future__ import annotations

import pytest

from orb.cli.factories.utility_command_factory import (
    InfrastructureCommandData,
    InitCommandData,
    MCPServeCommandData,
    MCPToolsCommandData,
    MCPValidateCommandData,
    ProviderOperationCommandData,
    StorageTestCommandData,
    TemplateUtilityCommandData,
    UtilityCommandFactory,
)


@pytest.fixture
def factory() -> UtilityCommandFactory:
    return UtilityCommandFactory()


# ---------------------------------------------------------------------------
# Data structure classes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitCommandData:
    def test_defaults(self):
        d = InitCommandData()
        assert d.interactive is False
        assert d.non_interactive is False
        assert d.scheduler is None
        assert d.provider is None
        assert d.config_dir is None
        assert d.force is False

    def test_override_kwargs(self):
        d = InitCommandData(interactive=True, force=True, scheduler="default")
        assert d.interactive is True
        assert d.force is True
        assert d.scheduler == "default"


@pytest.mark.unit
class TestMCPServeCommandData:
    def test_defaults(self):
        d = MCPServeCommandData()
        assert d.stdio is False
        assert d.port is None
        assert d.host == "localhost"
        assert d.log_level == "INFO"

    def test_override_kwargs(self):
        d = MCPServeCommandData(stdio=True, port=8080, host="0.0.0.0", log_level="DEBUG")
        assert d.stdio is True
        assert d.port == 8080
        assert d.host == "0.0.0.0"
        assert d.log_level == "DEBUG"


@pytest.mark.unit
class TestMCPToolsCommandData:
    def test_action_required(self):
        d = MCPToolsCommandData(action="list")
        assert d.action == "list"
        assert d.tool_name is None
        assert d.arguments == {}

    def test_tool_name_and_arguments(self):
        d = MCPToolsCommandData(action="call", tool_name="list_machines", arguments={"x": 1})
        assert d.tool_name == "list_machines"
        assert d.arguments == {"x": 1}


@pytest.mark.unit
class TestMCPValidateCommandData:
    def test_defaults(self):
        d = MCPValidateCommandData()
        assert d.config_path is None
        assert d.strict is False

    def test_override_kwargs(self):
        d = MCPValidateCommandData(config_path="/etc/mcp.yaml", strict=True)
        assert d.config_path == "/etc/mcp.yaml"
        assert d.strict is True


@pytest.mark.unit
class TestInfrastructureCommandData:
    def test_action_required(self):
        d = InfrastructureCommandData(action="discover")
        assert d.action == "discover"
        assert d.provider is None
        assert d.detailed is False

    def test_override_kwargs(self):
        d = InfrastructureCommandData(action="show", provider="aws", detailed=True)
        assert d.provider == "aws"
        assert d.detailed is True


@pytest.mark.unit
class TestProviderOperationCommandData:
    def test_action_required(self):
        d = ProviderOperationCommandData(action="exec")
        assert d.action == "exec"
        assert d.provider is None
        assert d.operation is None
        assert d.params is None

    def test_override_kwargs(self):
        d = ProviderOperationCommandData(
            action="select",
            provider="aws-prod",
            operation="health_check",
            params='{"k": "v"}',
        )
        assert d.provider == "aws-prod"
        assert d.operation == "health_check"


@pytest.mark.unit
class TestTemplateUtilityCommandData:
    def test_action_required(self):
        d = TemplateUtilityCommandData(action="refresh")
        assert d.action == "refresh"
        assert d.provider is None
        assert d.all_providers is False
        assert d.provider_api is None
        assert d.output_dir is None

    def test_override_kwargs(self):
        d = TemplateUtilityCommandData(action="generate", all_providers=True, output_dir="/tmp/out")
        assert d.all_providers is True
        assert d.output_dir == "/tmp/out"


@pytest.mark.unit
class TestStorageTestCommandData:
    def test_defaults(self):
        d = StorageTestCommandData()
        assert d.storage_type is None
        assert d.config_path is None
        assert d.verbose is False

    def test_override_kwargs(self):
        d = StorageTestCommandData(storage_type="sql", config_path="/cfg", verbose=True)
        assert d.storage_type == "sql"
        assert d.config_path == "/cfg"
        assert d.verbose is True


# ---------------------------------------------------------------------------
# UtilityCommandFactory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUtilityCommandFactory:
    def test_create_init_command_data(self, factory):
        data = factory.create_init_command_data(interactive=True)
        assert isinstance(data, InitCommandData)
        assert data.interactive is True

    def test_create_mcp_serve_command_data(self, factory):
        data = factory.create_mcp_serve_command_data(stdio=True)
        assert isinstance(data, MCPServeCommandData)
        assert data.stdio is True

    def test_create_mcp_tools_command_data(self, factory):
        data = factory.create_mcp_tools_command_data(action="list")
        assert isinstance(data, MCPToolsCommandData)
        assert data.action == "list"

    def test_create_mcp_validate_command_data(self, factory):
        data = factory.create_mcp_validate_command_data(strict=True)
        assert isinstance(data, MCPValidateCommandData)
        assert data.strict is True

    def test_create_infrastructure_command_data(self, factory):
        data = factory.create_infrastructure_command_data(action="discover")
        assert isinstance(data, InfrastructureCommandData)
        assert data.action == "discover"

    def test_create_provider_operation_command_data(self, factory):
        data = factory.create_provider_operation_command_data(action="exec")
        assert isinstance(data, ProviderOperationCommandData)
        assert data.action == "exec"

    def test_create_template_utility_command_data(self, factory):
        data = factory.create_template_utility_command_data(action="generate")
        assert isinstance(data, TemplateUtilityCommandData)
        assert data.action == "generate"

    def test_create_storage_test_command_data(self, factory):
        data = factory.create_storage_test_command_data(verbose=True)
        assert isinstance(data, StorageTestCommandData)
        assert data.verbose is True
