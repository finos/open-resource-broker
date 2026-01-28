"""Tests for request status caching functionality."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, PropertyMock, patch

import pytest

from application.dto.responses import RequestDTO
from config.manager import ConfigurationManager
from domain.base import UnitOfWorkFactory
from domain.base.ports import LoggingPort
from infrastructure.caching.request_cache_service import RequestCacheService


class TestRequestCacheService:
    """Test cases for RequestCacheService."""

    @pytest.fixture
    def mock_logger(self):
        """Mock logger for testing."""
        return Mock(spec=LoggingPort)

    @pytest.fixture
    def mock_uow_factory(self):
        """Mock unit of work factory for testing."""
        return Mock(spec=UnitOfWorkFactory)

    @pytest.fixture
    def mock_config_manager(self):
        """Mock configuration manager for testing."""
        config_manager = Mock(spec=ConfigurationManager)

        # Mock the app_config property with proper structure
        mock_app_config = Mock()
        mock_performance = Mock()
        mock_caching = Mock()
        mock_request_caching = Mock()
        mock_request_caching.enabled = True
        mock_request_caching.ttl_seconds = 300
        mock_caching.request_status_caching = mock_request_caching
        mock_performance.caching = mock_caching
        mock_app_config.performance = mock_performance

        config_manager.app_config = mock_app_config
        return config_manager

    @pytest.fixture
    def cache_service(self, mock_uow_factory, mock_config_manager, mock_logger):
        """Create cache service instance for testing."""
        return RequestCacheService(
            uow_factory=mock_uow_factory,
            config_manager=mock_config_manager,
            logger=mock_logger,
        )

    def test_caching_enabled_by_default(self, cache_service):
        """Test that caching is enabled when configured."""
        assert cache_service.is_caching_enabled() is True
        assert cache_service.get_cache_ttl() == 300

    def test_caching_disabled_when_config_missing(self, mock_uow_factory, mock_logger):
        """Test that caching is disabled when config is missing."""
        config_manager = Mock(spec=ConfigurationManager)

        # Mock app_config that raises exception when accessing performance
        mock_app_config = Mock()
        mock_app_config.performance = Mock()
        mock_app_config.performance.caching = Mock()
        mock_app_config.performance.caching.request_status_caching = Mock()
        mock_app_config.performance.caching.request_status_caching.enabled = False
        config_manager.app_config = mock_app_config

        cache_service = RequestCacheService(
            uow_factory=mock_uow_factory,
            config_manager=config_manager,
            logger=mock_logger,
        )

        assert cache_service.is_caching_enabled() is False

    def test_get_cached_request_when_disabled(self, mock_uow_factory, mock_logger):
        """Test that get_cached_request returns None when caching is disabled."""
        config_manager = Mock(spec=ConfigurationManager)

        # Mock disabled caching
        mock_app_config = Mock()
        mock_performance = Mock()
        mock_caching = Mock()
        mock_request_caching = Mock()
        mock_request_caching.enabled = False
        mock_caching.request_status_caching = mock_request_caching
        mock_performance.caching = mock_caching
        mock_app_config.performance = mock_performance
        config_manager.app_config = mock_app_config

        cache_service = RequestCacheService(
            uow_factory=mock_uow_factory,
            config_manager=config_manager,
            logger=mock_logger,
        )

        result = cache_service.get_cached_request("test-request-id")
        assert result is None

    def test_cache_request_when_disabled(self, mock_uow_factory, mock_logger):
        """Test that cache_request does nothing when caching is disabled."""
        config_manager = Mock(spec=ConfigurationManager)
        
        # Mock the app_config property structure
        mock_app_config = Mock()
        mock_performance = Mock()
        mock_caching = Mock()
        mock_request_caching = Mock()
        mock_request_caching.enabled = False
        mock_caching.request_status_caching = mock_request_caching
        mock_performance.caching = mock_caching
        mock_app_config.performance = mock_performance
        config_manager.app_config = mock_app_config

        cache_service = RequestCacheService(
            uow_factory=mock_uow_factory,
            config_manager=config_manager,
            logger=mock_logger,
        )

        request_dto = RequestDTO(
            request_id="test-request-id",
            template_id="test-template",
            requested_count=1,
            status="completed",
            created_at=datetime.now(timezone.utc),
            machine_references=[],
            metadata={},
        )

        # Should not raise any exceptions
        cache_service.cache_request(request_dto)

    @patch("infrastructure.caching.request_cache_service.datetime")
    def test_cache_validity_check(self, mock_datetime, cache_service):
        """Test cache validity checking logic."""
        # Mock current time
        current_time = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime.utcnow.return_value = current_time

        # Create mock request with recent update time (within TTL)
        mock_request = Mock()
        mock_request.updated_at = current_time - timedelta(seconds=200)  # 200 seconds ago

        # Should be valid (200 < 300 TTL)
        assert cache_service._is_cache_valid(mock_request) is True

        # Create mock request with old update time (outside TTL)
        mock_request.updated_at = current_time - timedelta(seconds=400)  # 400 seconds ago

        # Should be invalid (400 > 300 TTL)
        assert cache_service._is_cache_valid(mock_request) is False

        # Test with no updated_at
        mock_request.updated_at = None
        assert cache_service._is_cache_valid(mock_request) is False

    def test_config_error_handling(self, mock_uow_factory, mock_logger):
        """Test that config errors are handled gracefully."""
        config_manager = Mock(spec=ConfigurationManager)
        
        # Make app_config property raise exception when accessed
        type(config_manager).app_config = PropertyMock(side_effect=Exception("Config error"))

        cache_service = RequestCacheService(
            uow_factory=mock_uow_factory,
            config_manager=config_manager,
            logger=mock_logger,
        )

        # Should default to disabled
        assert cache_service.is_caching_enabled() is False
        assert cache_service.get_cache_ttl() == 300  # Default TTL

        # Should log warning
        mock_logger.warning.assert_called()


class TestRequestCacheIntegration:
    """Integration tests for request caching with query handlers."""

    def test_cache_service_initialization(self):
        """Test that cache service can be initialized properly."""
        mock_uow_factory = Mock(spec=UnitOfWorkFactory)
        mock_logger = Mock(spec=LoggingPort)
        mock_config_manager = Mock(spec=ConfigurationManager)
        
        # Mock the app_config property structure
        mock_app_config = Mock()
        mock_performance = Mock()
        mock_caching = Mock()
        mock_request_caching = Mock()
        mock_request_caching.enabled = True
        mock_request_caching.ttl_seconds = 600
        mock_caching.request_status_caching = mock_request_caching
        mock_performance.caching = mock_caching
        mock_app_config.performance = mock_performance
        mock_config_manager.app_config = mock_app_config

        cache_service = RequestCacheService(
            uow_factory=mock_uow_factory,
            config_manager=mock_config_manager,
            logger=mock_logger,
        )

        assert cache_service is not None
        assert cache_service.is_caching_enabled() is True
        assert cache_service.get_cache_ttl() == 600
