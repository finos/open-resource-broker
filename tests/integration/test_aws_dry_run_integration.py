"""Integration tests for AWS dry-run functionality."""

from unittest.mock import Mock, patch

import pytest

from src.infrastructure.mocking.dry_run_context import (
    dry_run_context,
    is_dry_run_active,
)
from src.providers.aws.infrastructure.dry_run_adapter import (
    aws_dry_run_context,
    get_aws_dry_run_status,
    is_aws_dry_run_active,
)
from src.providers.aws.managers.aws_instance_manager import AWSInstanceManager


@pytest.mark.integration
class TestAWSDryRunIntegration:
    """Integration tests for AWS dry-run functionality."""

    def test_aws_dry_run_context_activation(self):
        """Test that AWS dry-run context activates correctly."""
        assert not is_dry_run_active()
        assert not is_aws_dry_run_active()

        with dry_run_context(True):
            assert is_dry_run_active()
            assert is_aws_dry_run_active()  # Should be True if moto is available

        assert not is_dry_run_active()
        assert not is_aws_dry_run_active()

    def test_aws_dry_run_status(self):
        """Test AWS dry-run status information."""
        status = get_aws_dry_run_status()

        assert isinstance(status, dict)
        assert "dry_run_active" in status
        assert "moto_available" in status
        assert "aws_dry_run_active" in status
        assert "moto_version" in status

        # Initially not active
        assert status["dry_run_active"] is False
        assert status["aws_dry_run_active"] is False

        with dry_run_context(True):
            status = get_aws_dry_run_status()
            assert status["dry_run_active"] is True
            # aws_dry_run_active depends on moto availability

    def test_nested_aws_dry_run_contexts(self):
        """Test nested AWS dry-run contexts work correctly."""
        assert not is_dry_run_active()

        with dry_run_context(True):
            assert is_dry_run_active()

            with aws_dry_run_context():
                # Should still be in dry-run mode
                assert is_dry_run_active()

            assert is_dry_run_active()

        assert not is_dry_run_active()

    @patch("src.providers.aws.infrastructure.dry_run_adapter.MOTO_AVAILABLE", True)
    @patch("src.providers.aws.infrastructure.dry_run_adapter.mock_aws")
    def test_aws_dry_run_context_with_moto(self, mock_aws_decorator):
        """Test AWS dry-run context uses moto when available."""
        mock_context = Mock()
        mock_aws_decorator.return_value = mock_context
        mock_context.__enter__ = Mock()
        mock_context.__exit__ = Mock()

        # Test without global dry-run
        with aws_dry_run_context():
            pass

        # Should not use moto when dry-run is not active
        mock_aws_decorator.assert_not_called()

        # Test with global dry-run
        with dry_run_context(True):
            with aws_dry_run_context():
                pass

        # Should use moto when dry-run is active
        mock_aws_decorator.assert_called_once()

    @patch("src.providers.aws.infrastructure.dry_run_adapter.MOTO_AVAILABLE", False)
    def test_aws_dry_run_context_without_moto(self):
        """Test AWS dry-run context when moto is not available."""
        with dry_run_context(True):
            # Should not raise error even without moto
            with aws_dry_run_context():
                assert is_dry_run_active()
                assert not is_aws_dry_run_active()  # False because moto not available

    def test_aws_instance_manager_dry_run_integration(self):
        """Test AWS instance manager with dry-run context."""
        # Mock dependencies
        mock_aws_client = Mock()
        mock_config = Mock()
        mock_logger = Mock()

        # Create instance manager
        manager = AWSInstanceManager(mock_aws_client, mock_config, mock_logger)

        # Mock EC2 client and response
        mock_ec2_client = Mock()
        mock_aws_client.get_client.return_value = mock_ec2_client

        # Mock run_instances response
        mock_response = {
            "Instances": [
                {
                    "InstanceId": "i-1234567890abcdef0",
                    "State": {"Name": "pending"},
                    "InstanceType": "t2.micro",
                    "Placement": {"AvailabilityZone": "us-east-1a"},
                    "LaunchTime": "2023-01-01T00:00:00Z",
                    "VpcId": "vpc-12345678",
                    "SubnetId": "subnet-12345678",
                }
            ]
        }
        mock_ec2_client.run_instances.return_value = mock_response

        # Create template config (as expected by AWS instance manager)
        template_config = {
            "image_id": "ami-12345678",
            "vm_type": "t2.micro",
            "tags": {"Name": "test-instance"},
        }

        # Test without dry-run
        instance_ids = manager.create_instances(template_config, 1)
        assert len(instance_ids) == 1
        assert instance_ids[0] == "i-1234567890abcdef0"

        # Test with dry-run - should still work (mocked or real depending on moto)
        with dry_run_context(True):
            instance_ids = manager.create_instances(template_config, 1)
            assert len(instance_ids) == 1
            assert instance_ids[0] == "i-1234567890abcdef0"

    def test_aws_dry_run_context_thread_safety(self):
        """Test that AWS dry-run context is thread-safe."""
        import threading

        results = {}

        def thread_function(thread_id: int, use_dry_run: bool):
            """Function to run in separate thread."""
            if use_dry_run:
                with dry_run_context(True):
                    with aws_dry_run_context():
                        results[thread_id] = {
                            "dry_run_active": is_dry_run_active(),
                            "aws_dry_run_active": is_aws_dry_run_active(),
                        }
            else:
                with aws_dry_run_context():
                    results[thread_id] = {
                        "dry_run_active": is_dry_run_active(),
                        "aws_dry_run_active": is_aws_dry_run_active(),
                    }

        # Create threads with different dry-run states
        thread1 = threading.Thread(target=thread_function, args=(1, True))
        thread2 = threading.Thread(target=thread_function, args=(2, False))

        thread1.start()
        thread2.start()

        thread1.join()
        thread2.join()

        # Each thread should have its own state
        assert results[1]["dry_run_active"] is True
        assert results[2]["dry_run_active"] is False

        # Main thread should be unaffected
        assert not is_dry_run_active()
        assert not is_aws_dry_run_active()


@pytest.mark.integration
@pytest.mark.aws
class TestAWSDryRunWithMoto:
    """Integration tests that require moto to be available."""

    @pytest.fixture(autouse=True)
    def setup_moto(self):
        """Setup moto for testing."""
        pytest.importorskip("moto", reason="moto not available")

    def test_aws_dry_run_with_real_moto(self):
        """Test AWS dry-run with actual moto integration."""
        import boto3
        from moto import mock_aws

        # Test that moto actually works
        with mock_aws():
            ec2 = boto3.client("ec2", region_name="us-east-1")

            # This should work with moto
            response = ec2.describe_instances()
            assert "Reservations" in response
            assert response["Reservations"] == []

        # Test our dry-run context with moto
        with dry_run_context(True):
            with aws_dry_run_context():
                # This should be mocked
                ec2 = boto3.client("ec2", region_name="us-east-1")
                response = ec2.describe_instances()
                assert "Reservations" in response

    def test_aws_instance_creation_with_moto(self):
        """Test actual AWS instance creation with moto."""
        import boto3

        with dry_run_context(True):
            with aws_dry_run_context():
                ec2 = boto3.client("ec2", region_name="us-east-1")

                # Create instance with moto
                response = ec2.run_instances(
                    ImageId="ami-12345678",
                    MinCount=1,
                    MaxCount=1,
                    InstanceType="t2.micro",
                )

                assert "Instances" in response
                assert len(response["Instances"]) == 1
                instance = response["Instances"][0]
                assert instance["InstanceType"] == "t2.micro"
                assert instance["ImageId"] == "ami-12345678"
                assert "InstanceId" in instance
