"""Tests for BaseContextMixin."""

from unittest.mock import Mock, patch

from domain.request.aggregate import Request
from providers.aws.domain.template.aggregate import AWSTemplate
from providers.aws.infrastructure.handlers.base_context_mixin import BaseContextMixin


class TestableContextMixin(BaseContextMixin):
    """Testable implementation of BaseContextMixin."""

    config_port = None


class TestBaseContextMixin:
    """Test BaseContextMixin functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mixin = TestableContextMixin()

        # Mock template
        self.template = Mock(spec=AWSTemplate)
        self.template.template_id = "test-template-123"
        self.template.price_type = "ondemand"
        self.template.subnet_ids = ["subnet-123", "subnet-456"]
        self.template.security_group_ids = ["sg-123", "sg-456"]
        self.template.tags = {"Environment": "test", "Project": "hostfactory"}
        self.template.instance_types = {"t3.medium": 1, "t3.large": 2}

        # Mock request
        self.request = Mock(spec=Request)
        self.request.request_id = "req-test-456"
        self.request.requested_count = 5

    def test_prepare_base_context(self):
        """Test base context preparation."""
        with patch("providers.aws.infrastructure.handlers.base_context_mixin.datetime") as mock_dt:
            mock_dt.utcnow.return_value.isoformat.return_value = "2025-01-15T10:30:00Z"

            context = self.mixin._prepare_base_context(self.template, self.request)

            assert context["request_id"] == "req-test-456"
            assert context["template_id"] == "test-template-123"
            assert context["requested_count"] == 5
            assert context["min_count"] == 1
            assert context["max_count"] == 5
            assert context["timestamp"] == "2025-01-15T10:30:00Z"
            assert context["created_by"] == "open-hostfactory-plugin"

    def test_get_package_name_with_config_port(self):
        """Test package name retrieval with config port."""
        mock_config_port = Mock()
        mock_config_port.get_package_info.return_value = {"name": "custom-package-name"}
        self.mixin.config_port = mock_config_port

        result = self.mixin._get_package_name()

        assert result == "custom-package-name"
        mock_config_port.get_package_info.assert_called_once()

    def test_get_package_name_fallback(self):
        """Test package name retrieval fallback."""
        # No config port
        self.mixin.config_port = None

        result = self.mixin._get_package_name()

        assert result == "open-hostfactory-plugin"

    def test_get_package_name_exception_fallback(self):
        """Test package name retrieval with exception fallback."""
        mock_config_port = Mock()
        mock_config_port.get_package_info.side_effect = Exception("Config error")
        self.mixin.config_port = mock_config_port

        result = self.mixin._get_package_name()

        assert result == "open-hostfactory-plugin"

    def test_calculate_capacity_distribution_ondemand(self):
        """Test capacity distribution for on-demand only."""
        self.template.price_type = "ondemand"

        result = self.mixin._calculate_capacity_distribution(self.template, self.request)

        assert result["total_capacity"] == 5
        assert result["target_capacity"] == 5
        assert result["desired_capacity"] == 5
        assert result["on_demand_count"] == 5
        assert result["spot_count"] == 0
        assert result["is_heterogeneous"] is False
        assert result["is_spot_only"] is False
        assert result["is_ondemand_only"] is True

    def test_calculate_capacity_distribution_spot(self):
        """Test capacity distribution for spot only."""
        self.template.price_type = "spot"

        result = self.mixin._calculate_capacity_distribution(self.template, self.request)

        assert result["total_capacity"] == 5
        assert result["on_demand_count"] == 0
        assert result["spot_count"] == 5
        assert result["is_spot_only"] is True
        assert result["is_ondemand_only"] is False

    def test_calculate_capacity_distribution_heterogeneous(self):
        """Test capacity distribution for heterogeneous fleet."""
        self.template.price_type = "heterogeneous"
        self.template.percent_on_demand = 40

        result = self.mixin._calculate_capacity_distribution(self.template, self.request)

        assert result["total_capacity"] == 5
        assert result["on_demand_count"] == 2  # 40% of 5
        assert result["spot_count"] == 3  # 60% of 5
        assert result["is_heterogeneous"] is True

    def test_calculate_capacity_distribution_heterogeneous_default(self):
        """Test capacity distribution for heterogeneous fleet with default percentage."""
        self.template.price_type = "heterogeneous"
        self.template.percent_on_demand = None  # Should default to 0

        result = self.mixin._calculate_capacity_distribution(self.template, self.request)

        assert result["on_demand_count"] == 0
        assert result["spot_count"] == 5

    def test_prepare_standard_tags(self):
        """Test standard tag preparation."""
        with patch.object(self.mixin, "_get_package_name", return_value="test-package"):
            with patch(
                "providers.aws.infrastructure.handlers.base_context_mixin.datetime"
            ) as mock_dt:
                mock_dt.utcnow.return_value.isoformat.return_value = "2025-01-15T10:30:00Z"

                result = self.mixin._prepare_standard_tags(self.template, self.request)

                # Check base tags
                base_tags = result["base_tags"]
                assert len(base_tags) == 4
                assert {"key": "RequestId", "value": "req-test-456"} in base_tags
                assert {"key": "TemplateId", "value": "test-template-123"} in base_tags
                assert {"key": "CreatedBy", "value": "test-package"} in base_tags
                assert {"key": "CreatedAt", "value": "2025-01-15T10:30:00Z"} in base_tags

                # Check custom tags
                custom_tags = result["custom_tags"]
                assert len(custom_tags) == 2
                assert {"key": "Environment", "value": "test"} in custom_tags
                assert {"key": "Project", "value": "hostfactory"} in custom_tags

                # Check flags
                assert result["has_custom_tags"] is True
                assert len(result["all_tags"]) == 6

    def test_prepare_standard_tags_no_custom_tags(self):
        """Test standard tag preparation without custom tags."""
        self.template.tags = None

        with patch.object(self.mixin, "_get_package_name", return_value="test-package"):
            result = self.mixin._prepare_standard_tags(self.template, self.request)

            assert len(result["base_tags"]) == 4
            assert len(result["custom_tags"]) == 0
            assert result["has_custom_tags"] is False
            assert len(result["all_tags"]) == 4

    def test_prepare_standard_flags(self):
        """Test standard flag preparation."""
        # Add optional attributes
        self.template.key_name = "my-key"
        self.template.user_data = "#!/bin/bash"
        self.template.instance_profile = "my-profile"
        self.template.ebs_optimized = True
        self.template.monitoring_enabled = False

        result = self.mixin._prepare_standard_flags(self.template)

        assert result["has_subnets"] is True
        assert result["has_security_groups"] is True
        assert result["has_instance_types"] is True
        assert result["has_key_name"] is True
        assert result["has_user_data"] is True
        assert result["has_instance_profile"] is True
        assert result["has_ebs_optimized"] is True
        assert result["has_monitoring"] is True

    def test_prepare_standard_flags_minimal(self):
        """Test standard flag preparation with minimal template."""
        self.template.subnet_ids = None
        self.template.security_group_ids = None
        self.template.instance_types = None

        result = self.mixin._prepare_standard_flags(self.template)

        assert result["has_subnets"] is False
        assert result["has_security_groups"] is False
        assert result["has_instance_types"] is False
        assert result["has_key_name"] is False
        assert result["has_user_data"] is False
        assert result["has_instance_profile"] is False
        assert result["has_ebs_optimized"] is False
        assert result["has_monitoring"] is False
