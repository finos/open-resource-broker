"""Record of a template that could not be loaded."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TemplateRejection:
    """A template that was skipped at load because it failed validation.

    Attributes:
        template_id: Identifier of the rejected template.
        reason: Why the template was rejected.
        row: The resolved template dict that failed conversion.
    """

    template_id: str
    reason: str
    row: dict[str, Any] = field(default_factory=dict)
