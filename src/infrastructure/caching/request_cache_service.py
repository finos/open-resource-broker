"""Request status caching service using database storage."""

from datetime import datetime, timedelta
from typing import Optional

from application.dto.responses import RequestDTO
from config.manager import ConfigurationManager
from domain.base import UnitOfWorkFactory
from domain.base.ports import LoggingPort


class RequestCacheService:
    """Database-based request status caching service."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        config_manager: ConfigurationManager,
        logger: LoggingPort,
    ) -> None:
        """Initialize the instance."""
        self.uow_factory = uow_factory
        self.config_manager = config_manager
        self.logger = logger
        self._cache_enabled = self._is_caching_enabled()
        self._ttl_seconds = self._get_cache_ttl()

    def _is_caching_enabled(self) -> bool:
        """Check if request status caching is enabled."""
        try:
            config = self.config_manager.get_app_config()
            caching_config = config.get("performance", {}).get("caching", {})
            request_caching = caching_config.get("request_status_caching", {})
            return request_caching.get("enabled", False)
        except Exception as e:
            self.logger.warning("Failed to get caching config, defaulting to disabled: %s", e)
            return False

    def _get_cache_ttl(self) -> int:
        """Get cache TTL in seconds."""
        try:
            config = self.config_manager.get_app_config()
            caching_config = config.get("performance", {}).get("caching", {})
            request_caching = caching_config.get("request_status_caching", {})
            return request_caching.get("ttl_seconds", 300)  # Default 5 minutes
        except Exception as e:
            self.logger.warning("Failed to get cache TTL, defaulting to 300 seconds: %s", e)
            return 300

    def get_cached_request(self, request_id: str) -> Optional[RequestDTO]:
        """Get request from cache if within TTL."""
        if not self._cache_enabled:
            return None

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                from domain.request.value_objects import RequestId

                request_id_obj = RequestId(value=request_id)
                request = uow.requests.get_by_id(request_id_obj)

                if not request:
                    return None

                # Check if cache is still valid
                if self._is_cache_valid(request):
                    # Get machines for the request
                    machines = uow.machines.find_by_request_id(request_id)

                    # Convert to DTO format
                    machines_data = []
                    for machine in machines:
                        machines_data.append(
                            {
                                "instance_id": str(machine.instance_id),
                                "status": machine.status.value,
                                "private_ip": machine.private_ip,
                                "public_ip": machine.public_ip,
                                "launch_time": machine.launch_time,
                                "launch_time_timestamp": (
                                    machine.launch_time.timestamp() if machine.launch_time else 0
                                ),
                            }
                        )

                    request_dto = RequestDTO(
                        request_id=str(request.request_id),
                        template_id=request.template_id,
                        machine_count=request.requested_count,
                        status=request.status.value,
                        created_at=request.created_at,
                        machines=machines_data,
                        metadata=request.metadata or {},
                    )

                    self.logger.debug("Cache hit for request %s", request_id)
                    return request_dto
                else:
                    self.logger.debug("Cache expired for request %s", request_id)
                    return None

        except Exception as e:
            self.logger.warning("Failed to get cached request %s: %s", request_id, e)
            return None

    def cache_request(self, request_dto: RequestDTO) -> None:
        """Cache request status in database."""
        if not self._cache_enabled:
            return

        try:
            # The request is already stored in the database by the command handler
            # We just need to update the timestamp to mark it as "cached"
            with self.uow_factory.create_unit_of_work() as uow:
                from domain.request.value_objects import RequestId

                request_id_obj = RequestId(value=request_dto.request_id)
                request = uow.requests.get_by_id(request_id_obj)

                if request:
                    # Update the request's updated_at timestamp to mark cache time
                    request.updated_at = datetime.utcnow()
                    uow.requests.save(request)

                    self.logger.debug("Cached request %s", request_dto.request_id)

        except Exception as e:
            self.logger.warning("Failed to cache request %s: %s", request_dto.request_id, e)

    def _is_cache_valid(self, request) -> bool:
        """Check if cached request is within TTL."""
        if not request.updated_at:
            return False

        cache_age = datetime.utcnow() - request.updated_at
        return cache_age.total_seconds() < self._ttl_seconds

    def invalidate_cache(self, request_id: str) -> None:
        """Invalidate cache for a specific request."""
        try:
            with self.uow_factory.create_unit_of_work() as uow:
                from domain.request.value_objects import RequestId

                request_id_obj = RequestId(value=request_id)
                request = uow.requests.get_by_id(request_id_obj)

                if request:
                    # Set updated_at to a very old timestamp to invalidate cache
                    request.updated_at = datetime.utcnow() - timedelta(days=1)
                    uow.requests.save(request)

                    self.logger.debug("Invalidated cache for request %s", request_id)

        except Exception as e:
            self.logger.warning("Failed to invalidate cache for request %s: %s", request_id, e)

    def is_caching_enabled(self) -> bool:
        """Check if caching is enabled."""
        return self._cache_enabled

    def get_cache_ttl(self) -> int:
        """Get cache TTL in seconds."""
        return self._ttl_seconds
