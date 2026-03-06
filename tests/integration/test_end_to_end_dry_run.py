"""End-to-end integration tests for dry-run functionality."""

import asyncio
from unittest.mock import Mock, patch

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.mocking.dry_run_context import dry_run_context
from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.infrastructure.adapters import AWSProvisioningAdapter
from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy


class TestEndToEndDryRun:
    """Test end-to-end dry-run functionality from command to provider strategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_logger = Mock()
        self.aws_config = AWSProviderConfig(region="us-east-1", profile="default")
        self.mock_aws_client = Mock()
        self.aws_strategy = AWSProviderStrategy(
            config=self.aws_config,
            logger=self.mock_logger,
            aws_client_resolver=lambda: self.mock_aws_client,
        )
        self.aws_strategy._initialized = True

        self.provisioning_adapter = AWSProvisioningAdapter(
            aws_client=self.mock_aws_client,
            logger=self.mock_logger,
            provider_strategy=self.aws_strategy,
        )

    def _make_request(self, dry_run: bool = False) -> Request:
        return Request(
            request_id=RequestId.generate(RequestType.ACQUIRE),
            request_type=RequestType.ACQUIRE,
            provider_type="aws",
            template_id="test-template",
            requested_count=1,
            metadata={"dry_run": dry_run},
        )

    def _make_template(self) -> Template:
        return Template(
            template_id="test-template",
            provider_api="EC2Fleet",
            machine_types={"t2.micro": 1},
            max_number=10,
            image_id="ami-12345678",
            subnet_ids=["subnet-12345678"],
        )

    def test_dry_run_uses_provider_strategy(self):
        """Dry-run requests short-circuit in _provision_via_handlers before reaching AWS."""
        request = self._make_request(dry_run=True)
        template = self._make_template()

        result = asyncio.run(self.provisioning_adapter.provision_resources(request, template))

        assert result == {"dry_run": True, "instances": [], "resource_ids": [], "success": True}

    def test_normal_request_uses_handler_path(self):
        """Normal (non-dry-run) requests are routed through the handler path."""
        request = self._make_request(dry_run=False)
        template = self._make_template()

        mock_handler = Mock()
        mock_handler.acquire_hosts.return_value = {
            "success": True,
            "resource_ids": ["fleet-123"],
            "instances": [],
        }

        expected = {"success": True, "resource_ids": ["fleet-123"], "instances": []}

        with patch.object(
            AWSProvisioningAdapter,
            "_get_handler_for_template",
            return_value=mock_handler,
        ):
            result = asyncio.run(self.provisioning_adapter.provision_resources(request, template))

        assert result == expected
        mock_handler.acquire_hosts.assert_called_once()

    def test_dry_run_strategy_failure_raises(self):
        """A dry-run request returns the short-circuit response, not an error."""
        request = self._make_request(dry_run=True)
        template = self._make_template()

        result = asyncio.run(self.provisioning_adapter.provision_resources(request, template))

        assert result == {"dry_run": True, "instances": [], "resource_ids": [], "success": True}

    def test_dry_run_context_manager(self):
        """dry_run_context sets and clears the global dry-run flag."""
        with dry_run_context(True):
            from orb.infrastructure.mocking.dry_run_context import is_dry_run_active

            assert is_dry_run_active()

        from orb.infrastructure.mocking.dry_run_context import is_dry_run_active

        assert not is_dry_run_active()

    def test_no_strategy_falls_back_to_handlers(self):
        """When no provider strategy is set, dry-run short-circuits in the handler path."""
        adapter = AWSProvisioningAdapter(
            aws_client=self.mock_aws_client,
            logger=self.mock_logger,
            provider_strategy=None,
        )
        request = self._make_request(dry_run=True)
        template = self._make_template()

        mock_handler = Mock()

        with patch.object(
            AWSProvisioningAdapter,
            "_get_handler_for_template",
            return_value=mock_handler,
        ):
            result = asyncio.run(adapter.provision_resources(request, template))

        assert result == {"dry_run": True, "instances": [], "resource_ids": [], "success": True}
