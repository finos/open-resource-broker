"""Provider operation result classes - Re-exports from domain layer."""

# Re-export domain results for backward compatibility
from orb.domain.base.results import ProviderSelectionResult, ValidationLevel, ValidationResult

__all__ = ["ProviderSelectionResult", "ValidationLevel", "ValidationResult"]
