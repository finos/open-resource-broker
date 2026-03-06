"""Provider name generation and parsing service."""

from typing import Any, Dict

from orb.domain.base.ports.provider_name_port import ProviderNamePort
from orb.domain.base.utils import extract_provider_type


class ProviderNameService:
    """Service for generating and parsing provider names."""

    def __init__(self, provider_name_port: ProviderNamePort):
        self._provider_name_port = provider_name_port

    def generate_provider_name(self, provider_type: str, config: Dict[str, Any]) -> str:
        return self._provider_name_port.generate_provider_name(provider_type, config)

    def parse_provider_name(self, provider_name: str) -> Dict[str, str]:
        return self._provider_name_port.parse_provider_name(provider_name)

    def get_provider_name_pattern(self, provider_type: str) -> str:
        return self._provider_name_port.get_provider_name_pattern(provider_type)

    def _extract_provider_type(self, provider_name: str) -> str:
        return extract_provider_type(provider_name)
