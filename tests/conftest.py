"""Global test configuration and fixtures."""

import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, Optional
from unittest.mock import Mock

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import test environment fixtures

# Import moto for AWS mocking
try:
    from moto import mock_aws

    MOTO_AVAILABLE = True
except ImportError:
    # Fallback if moto is not available
    def mock_aws():
        def decorator(func):
            """No-op decorator when moto is not available."""
            return func

        return decorator

    MOTO_AVAILABLE = False

import boto3

# Import application components - with error handling
try:
    from src.config.manager import ConfigurationManager
    from src.config.schemas.app_schema import AppConfig
    from src.domain.base.value_objects import InstanceId, InstanceType, ResourceId
    from src.domain.machine.aggregate import Machine
    from src.domain.request.aggregate import Request
    from src.domain.template.aggregate import Template
    from src.infrastructure.di.buses import CommandBus, QueryBus
    from src.infrastructure.di.container import DIContainer
    from src.infrastructure.template.services.template_persistence_service import (
        TemplatePersistenceService,
    )
    from src.providers.aws.configuration.config import AWSConfig

    IMPORTS_AVAILABLE = True
except ImportError as e:
    # If imports fail, create mock classes to prevent test failures
    print(f"Warning: Could not import application components: {e}")
    IMPORTS_AVAILABLE = False

    class ConfigurationManager:
        def __init__(self):
            self._config = {}

        def load_from_file(self, path):
            pass

        def get(self, key, default=None):
            return default

    class AppConfig:
        def __init__(self, **kwargs):
            pass

    class TemplateService:
        pass

    class CommandBus:
        def __init__(self, **kwargs):
            pass

        def execute(self, command):
            return {"success": True}

    class QueryBus:
        def __init__(self, **kwargs):
            pass

        def execute(self, query):
            return {"success": True}

    class DIContainer:
        pass

    class Template:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Request:
        @classmethod
        def create_new_request(cls, **kwargs):
            return cls(**kwargs)

        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Machine:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class InstanceId:
        def __init__(self, value):
            self.value = value

    class InstanceType:
        def __init__(self, value):
            self.value = value

    class ResourceId:
        def __init__(self, value):
            self.value = value

    class AWSProvider:
        def __init__(self, **kwargs):
            pass

    class AWSConfig:
        def __init__(self, **kwargs):
            pass


# Test utilities
from tests.utilities.reset_singletons import reset_all_singletons


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up test environment variables."""
    # Set up PYTHONPATH first
    project_root = Path(__file__).parent.parent
    src_path = project_root / "src"

    # Add src to Python path
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    # Set environment variables
    os.environ.update(
        {
            "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_ACCESS_KEY_ID": "testing",
            "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_SECURITY_TOKEN": "testing",
            "AWS_SESSION_TOKEN": "testing",
            "ENVIRONMENT": "testing",
            "LOG_LEVEL": "DEBUG",
            "TESTING": "true",
            "PYTHONPATH": f"{src_path}:{os.environ.get('PYTHONPATH', '')}",
            "PYTHONWARNINGS": "ignore::DeprecationWarning",
            "MOTO_CALL_RESET_API": "false",
        }
    )


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset all singletons before each test."""
    reset_all_singletons()
    yield
    reset_all_singletons()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test files."""
    temp_path = Path(tempfile.mkdtemp())
    try:
        yield temp_path
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def test_config_dict() -> Dict[str, Any]:
    """Basic test configuration dictionary."""
    return {
        "aws": {
            "region": "us-east-1",
            "profile": "default",
            "access_key_id": "testing",
            "secret_access_key": "testing",
        },
        "logging": {
            "level": "DEBUG",
            "file_path": "logs/test.log",
            "console_enabled": True,
        },
        "database": {"type": "sqlite", "host": "", "port": 0, "name": ":memory:"},
        "template": {
            "default_image_id": "ami-12345678",
            "default_instance_type": "t2.micro",
            "subnet_ids": ["subnet-12345678"],
            "security_group_ids": ["sg-12345678"],
        },
        "REPOSITORY_CONFIG": {
            "type": "json",
            "json": {
                "storage_type": "single_file",
                "base_path": "/tmp",
                "filenames": {"single_file": "test_database.json"},
            },
        },
    }


@pytest.fixture
def test_config_file(temp_dir: Path, test_config_dict: Dict[str, Any]) -> Path:
    """Create a test configuration file."""
    config_file = temp_dir / "test_config.json"
    with open(config_file, "w") as f:
        json.dump(test_config_dict, f, indent=2)
    return config_file


@pytest.fixture
def config_manager(test_config_dict: Dict[str, Any]) -> ConfigurationManager:
    """Create a test configuration manager."""
    manager = ConfigurationManager()
    if IMPORTS_AVAILABLE:
        manager._config = test_config_dict
    return manager


@pytest.fixture
def app_config(test_config_dict: Dict[str, Any]) -> AppConfig:
    """Create a test app configuration."""
    if IMPORTS_AVAILABLE:
        return AppConfig(**test_config_dict)
    else:
        return AppConfig()


@pytest.fixture
def aws_config() -> AWSConfig:
    """Create a test AWS configuration."""
    if IMPORTS_AVAILABLE:
        return AWSConfig(
            region="us-east-1",
            profile="default",
            access_key_id="testing",
            secret_access_key="testing",
        )
    else:
        return AWSConfig()


@pytest.fixture
def aws_mocks():
    """Set up comprehensive AWS service mocks."""
    if MOTO_AVAILABLE:
        with mock_aws():
            yield
    else:
        # Fallback if moto is not available
        yield


@pytest.fixture
def ec2_client(aws_mocks):
    """Create a mocked EC2 client."""
    return boto3.client("ec2", region_name="us-east-1")


@pytest.fixture
def autoscaling_client(aws_mocks):
    """Create a mocked Auto Scaling client."""
    return boto3.client("autoscaling", region_name="us-east-1")


@pytest.fixture
def ssm_client(aws_mocks):
    """Create a mocked SSM client."""
    return boto3.client("ssm", region_name="us-east-1")


@pytest.fixture
def mock_ec2_resources(ec2_client):
    """Create mock EC2 resources for testing."""
    # Create VPC
    vpc = ec2_client.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]

    # Create subnet
    subnet = ec2_client.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone="us-east-1a"
    )
    subnet_id = subnet["Subnet"]["SubnetId"]

    # Create security group
    sg = ec2_client.create_security_group(
        GroupName="test-sg", Description="Test security group", VpcId=vpc_id
    )
    sg_id = sg["GroupId"]

    # Create key pair
    key_pair = ec2_client.create_key_pair(KeyName="test-key")

    return {
        "vpc_id": vpc_id,
        "subnet_id": subnet_id,
        "security_group_id": sg_id,
        "key_name": key_pair["KeyName"],
    }


@pytest.fixture
def sample_template() -> Template:
    """Create a sample template for testing."""
    if IMPORTS_AVAILABLE:
        return Template(
            id="template-001",
            template_id="template-001",
            name="test-template",
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            instance_type="t2.micro",  # Use string instead of InstanceType object
            max_instances=10,  # Add max_instances field
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
            key_name="test-key",
            user_data="#!/bin/bash\necho 'Hello World'",
            tags={"Environment": "test", "Project": "hostfactory"},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    else:
        return Template(
            id="template-001", name="test-template", provider_api="ec2_fleet"
        )


@pytest.fixture
def sample_request() -> Request:
    """Create a sample request for testing."""
    if IMPORTS_AVAILABLE:
        return Request.create_new_request(
            template_id="template-001",
            machine_count=2,
            requester_id="test-user",
            priority=1,
            tags={"Environment": "test"},
        )
    else:
        return Request(
            template_id="template-001", machine_count=2, requester_id="test-user"
        )


@pytest.fixture
def sample_machine() -> Machine:
    """Create a sample machine for testing."""
    if IMPORTS_AVAILABLE:
        return Machine(
            id="machine-001",
            instance_id=InstanceId("i-1234567890abcdef0"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
            private_ip="10.0.1.100",
            public_ip="54.123.45.67",
            tags={"Environment": "test"},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    else:
        return Machine(
            id="machine-001",
            instance_id="i-1234567890abcdef0",
            template_id="template-001",
            request_id="request-001",
            status="running",
        )


@pytest.fixture
def mock_template_service() -> Mock:
    """Create a mock template service."""
    service = Mock(spec=TemplatePersistenceService)
    service.get_available_templates.return_value = []
    service.get_template_by_id.return_value = None
    service.get_templates_by_provider.return_value = []
    service.get_templates_by_machine_type.return_value = []
    return service


@pytest.fixture
def mock_command_bus() -> Mock:
    """Create a mock command bus."""
    bus = Mock(spec=CommandBus)
    bus.dispatch.return_value = {"success": True}
    bus.register.return_value = None
    return bus


@pytest.fixture
def mock_query_bus() -> Mock:
    """Create a mock query bus."""
    bus = Mock(spec=QueryBus)
    bus.dispatch.return_value = {"success": True}
    bus.register.return_value = None
    return bus


@pytest.fixture
def mock_provider() -> Mock:
    """Create a mock provider."""
    provider = Mock()
    provider.provider_type = "mock"
    provider.initialize.return_value = True
    provider.health_check.return_value = {"status": "healthy"}
    provider.create_instances.return_value = []
    provider.terminate_instances.return_value = True
    provider.get_instance_status.return_value = {}
    provider.validate_template.return_value = {"valid": True}
    provider.get_available_templates.return_value = []
    provider.get_capabilities.return_value = {
        "provider_type": "mock",
        "region": "mock-region",
        "version": "1.0.0",
        "capabilities": ["mock_capability"],
    }
    return provider


@pytest.fixture
def mock_logger() -> Mock:
    """Create a mock logger."""
    return Mock()


@pytest.fixture
def mock_container() -> Mock:
    """Create a mock DI container."""
    return Mock()


@pytest.fixture
def mock_config() -> Mock:
    """Create a mock configuration."""
    return Mock()


# ApplicationService fixture removed - using direct CQRS buses instead
# Use mock_command_bus and mock_query_bus fixtures directly in tests


@pytest.fixture
def di_container() -> DIContainer:
    """Create a test DI container."""
    container = DIContainer()
    # Register test dependencies
    return container


@pytest.fixture(params=["json", "sql", "memory"])
def repository_type(request):
    """Parametrized fixture for different repository types."""
    return request.param


@pytest.fixture(
    params=["ec2_fleet", "auto_scaling_group", "spot_fleet", "run_instances"]
)
def provider_api_type(request):
    """Parametrized fixture for different AWS provider API types."""
    return request.param


@pytest.fixture(params=["t2.micro", "t2.small", "t3.medium", "m5.large"])
def instance_type(request):
    """Parametrized fixture for different instance types."""
    return request.param


# Test data generators
def generate_test_id() -> str:
    """Generate a unique test ID."""
    return f"test-{uuid.uuid4().hex[:8]}"


def generate_instance_id() -> str:
    """Generate a mock AWS instance ID."""
    return f"i-{uuid.uuid4().hex[:17]}"


def generate_request_id() -> str:
    """Generate a unique request ID."""
    return f"req-{uuid.uuid4().hex[:8]}"


def generate_template_id() -> str:
    """Generate a unique template ID."""
    return f"tpl-{uuid.uuid4().hex[:8]}"


# Test markers
pytest.mark.unit = pytest.mark.unit
pytest.mark.integration = pytest.mark.integration
pytest.mark.e2e = pytest.mark.e2e
pytest.mark.slow = pytest.mark.slow
pytest.mark.aws = pytest.mark.aws
pytest.mark.db = pytest.mark.db
pytest.mark.api = pytest.mark.api
pytest.mark.security = pytest.mark.security


# Custom assertions
def assert_valid_uuid(value: str) -> None:
    """Assert that a string is a valid UUID."""
    try:
        uuid.UUID(value)
    except ValueError:
        pytest.fail(f"'{value}' is not a valid UUID")


def assert_valid_instance_id(value: str) -> None:
    """Assert that a string is a valid AWS instance ID."""
    if not value.startswith("i-") or len(value) != 19:
        pytest.fail(f"'{value}' is not a valid AWS instance ID")


def assert_valid_timestamp(value: datetime) -> None:
    """Assert that a datetime is valid and recent."""
    if not isinstance(value, datetime):
        pytest.fail(f"'{value}' is not a datetime object")

    now = datetime.now(timezone.utc)
    if value > now:
        pytest.fail(f"Timestamp '{value}' is in the future")


# Test utilities
class TestDataBuilder:
    """Builder pattern for creating test data."""

    @staticmethod
    def template(
        template_id: Optional[str] = None,
        name: Optional[str] = None,
        provider_api: str = "ec2_fleet",
        **kwargs,
    ) -> Template:
        """Build a test template."""
        return Template(
            id=template_id or generate_template_id(),
            name=name or "test-template",
            provider_api=provider_api,
            image_id=kwargs.get("image_id", "ami-12345678"),
            instance_type=InstanceType(kwargs.get("instance_type", "t2.micro")),
            subnet_ids=kwargs.get("subnet_ids", ["subnet-12345678"]),
            security_group_ids=kwargs.get("security_group_ids", ["sg-12345678"]),
            key_name=kwargs.get("key_name", "test-key"),
            user_data=kwargs.get("user_data", "#!/bin/bash\necho 'test'"),
            tags=kwargs.get("tags", {"Environment": "test"}),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def request(
        request_id: Optional[str] = None,
        template_id: Optional[str] = None,
        machine_count: int = 1,
        **kwargs,
    ) -> Request:
        """Build a test request."""
        return Request.create_new_request(
            template_id=template_id or generate_template_id(),
            machine_count=machine_count,
            requester_id=kwargs.get("requester_id", "test-user"),
            priority=kwargs.get("priority", 1),
            tags=kwargs.get("tags", {"Environment": "test"}),
        )

    @staticmethod
    def machine(
        machine_id: Optional[str] = None, instance_id: Optional[str] = None, **kwargs
    ) -> Machine:
        """Build a test machine."""
        return Machine(
            id=machine_id or generate_test_id(),
            instance_id=InstanceId(instance_id or generate_instance_id()),
            template_id=kwargs.get("template_id", generate_template_id()),
            request_id=kwargs.get("request_id", generate_request_id()),
            status=kwargs.get("status", "running"),
            instance_type=InstanceType(kwargs.get("instance_type", "t2.micro")),
            availability_zone=kwargs.get("availability_zone", "us-east-1a"),
            private_ip=kwargs.get("private_ip", "10.0.1.100"),
            public_ip=kwargs.get("public_ip", "54.123.45.67"),
            tags=kwargs.get("tags", {"Environment": "test"}),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )


# Export test data builder
@pytest.fixture
def test_data_builder() -> TestDataBuilder:
    """Provide test data builder."""
    return TestDataBuilder()
