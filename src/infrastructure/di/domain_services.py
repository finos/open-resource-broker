"""Domain services registration for DI container."""

from infrastructure.di.container import DIContainer
from domain.services.timestamp_service import TimestampService
from domain.services.filter_service import FilterService
from infrastructure.services.iso_timestamp_service import ISOTimestampService
from infrastructure.services.machine_filter_service import MachineFilterService


def register_domain_services(container: DIContainer) -> None:
    """Register domain services with their implementations."""
    
    # Timestamp service
    container.register_singleton(TimestampService, ISOTimestampService)
    
    # Filter service
    container.register_singleton(FilterService, MachineFilterService)
