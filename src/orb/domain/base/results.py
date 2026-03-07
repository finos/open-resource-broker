"""Domain result classes."""

from dataclasses import dataclass
from enum import Enum


@dataclass
class ProviderSelectionResult:
    """Result of provider selection process."""

    provider_type: str
    provider_name: str
    selection_reason: str
    confidence: float = 1.0
    alternatives: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.alternatives is None:
            self.alternatives = []

    @property
    def provider_instance(self) -> str:
        """Get provider instance name (alias for provider_name)."""
        return self.provider_name


class ValidationLevel(str, Enum):
    """Validation strictness levels."""

    STRICT = "strict"
    PERMISSIVE = "permissive"
    WARN_ONLY = "warn_only"


@dataclass
class ValidationResult:
    """Result of template capability validation."""

    is_valid: bool
    provider_instance: str
    errors: list[str]
    warnings: list[str]
    supported_features: list[str]
    unsupported_features: list[str]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
