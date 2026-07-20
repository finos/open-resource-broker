"""Shared base for provider template-extension configuration.

Provider template extensions declare provider-specific template configuration
that is applied to templates through the hierarchical defaults system.  This
base consolidates the two behaviours every extension shares:

  * ``model_config = ConfigDict(extra="ignore")`` so unknown keys in a
    provider config section are dropped rather than raising.
  * a default :meth:`to_template_defaults` that emits every non-``None`` field
    as a defaults dict (flattening any nested *model* into the top level, while
    preserving plain ``dict``-valued fields such as ``env`` / ``node_selector``
    as first-class keys).

Concrete extensions (e.g. the AWS / k8s extension configs) may subclass this
and add provider-specific fields and validators; they inherit the defaults
projection for free and can override :meth:`to_template_defaults` when they
need bespoke flattening.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class ProviderTemplateExtensionBase(BaseModel):
    """Base class for provider-specific template extension configuration."""

    model_config = ConfigDict(extra="ignore")

    def to_template_defaults(self) -> dict[str, Any]:
        """Project non-``None`` fields to a flat template-defaults dict.

        Nested :class:`~pydantic.BaseModel` fields (config groups) are flattened
        into the top-level defaults so their inner keys surface as first-class
        default keys.  Plain ``dict``-valued fields (e.g. ``env``,
        ``node_selector``, ``resource_requests``) are preserved under their own
        field name — they are values, not config groups, so flattening them
        would drop the field and merge their contents into the top level.
        ``None`` values are omitted so they never override lower-precedence
        defaults.
        """
        # Field names whose declared annotation is a nested BaseModel; only
        # these are flattened.  Determined from the model's fields so a dumped
        # nested model (which becomes a plain dict) is not confused with a
        # genuine dict-valued field.
        nested_model_fields = {
            name
            for name, field in type(self).model_fields.items()
            if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
        }

        defaults: dict[str, Any] = {}
        for field_name, field_value in self.model_dump().items():
            if field_value is None:
                continue
            if field_name in nested_model_fields and isinstance(field_value, dict):
                defaults.update(field_value)
            else:
                defaults[field_name] = field_value
        return defaults


__all__ = ["ProviderTemplateExtensionBase"]
