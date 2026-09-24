"""Template Defaults Service - Hierarchical template default resolution with domain extensions."""

from typing import Any, Optional

from pydantic import BaseModel

from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort
from orb.domain.base.utils import extract_provider_type
from orb.domain.template.factory import TemplateFactoryPort
from orb.domain.template.ports.template_defaults_port import TemplateDefaultsPort
from orb.domain.template.ports.template_extension_registry_port import (
    TemplateExtensionRegistryPort,
)
from orb.domain.template.template_aggregate import Template


def _alias_groups_for_model(model: type[BaseModel]) -> dict[str, tuple[str, ...]]:
    """Map every ``AliasChoices`` field name on *model* to its full alias set.

    A field may be declared under a canonical name plus one or more deprecated
    aliases via Pydantic ``AliasChoices`` (e.g. ``machine_image`` accepts the
    legacy ``image_id``).  Derived from the model's own ``model_fields`` so it
    can never drift from the model definition.  This walks *only* the given
    model class, so calling it on a provider template subclass (e.g.
    ``AWSTemplate``) picks up top-level alias fields the base ``Template`` does
    not declare (e.g. ``abis_instance_requirements`` /
    ``abisInstanceRequirements``), and calling it on a provider *extension*
    config (e.g. ``K8sTemplateExtensionConfig``) picks up extension-only aliases
    such as ``env`` / ``environment_variables``.
    """
    from pydantic import AliasChoices

    groups: dict[str, tuple[str, ...]] = {}
    for field in model.model_fields.values():
        va = field.validation_alias
        if isinstance(va, AliasChoices):
            names = tuple(c for c in va.choices if isinstance(c, str))
            for name in names:
                groups[name] = names
    return groups


def _union_alias_groups(
    group_maps: list[dict[str, tuple[str, ...]]],
) -> dict[str, tuple[str, ...]]:
    """Union alias-group maps from several template models into one.

    Field aliases describe the *same logical slot* across the base model and
    any provider subclass, so overlapping groups are merged via their
    transitive closure: if any name is shared between two groups, every name in
    both belongs to the combined slot.  The result maps each alias name to the
    complete set of names for its slot, so a value under any one alias can drop
    every sibling regardless of which model contributed the alias.
    """
    # Union-find over alias names to compute connected components.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        # Path compression.
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for gm in group_maps:
        for names in gm.values():
            for name in names:
                union(names[0], name)

    components: dict[str, list[str]] = {}
    for name in parent:
        components.setdefault(find(name), []).append(name)

    result: dict[str, tuple[str, ...]] = {}
    for members in components.values():
        group = tuple(members)
        for name in members:
            result[name] = group
    return result


# Base-model alias groups — used as a static fallback when no provider template
# factory is injected.  The service unions these with provider-subclass alias
# groups at merge time via :meth:`TemplateDefaultsService._effective_alias_groups`.
_FIELD_ALIAS_GROUPS: dict[str, tuple[str, ...]] = _alias_groups_for_model(Template)


class TemplateDefaultsService(TemplateDefaultsPort):
    """
    Service for resolving hierarchical template defaults.

    Implements the following precedence hierarchy:
    1. Template file values (highest priority)
    2. Provider instance defaults
    3. Provider type defaults
    4. Global template defaults (lowest priority)

    This service ensures that templates get appropriate defaults applied
    while respecting the configuration hierarchy.
    """

    def __init__(
        self,
        config_manager: ConfigurationPort,
        logger: LoggingPort,
        template_factory: Optional[TemplateFactoryPort] = None,
        extension_registry: Optional[TemplateExtensionRegistryPort] = None,
        provider_registry: Optional[ProviderRegistryPort] = None,
    ) -> None:
        """
        Initialize the template defaults service.

        Args:
            config_manager: Configuration port for accessing defaults
            logger: Logger for debugging and monitoring
            template_factory: Factory for creating domain templates
            extension_registry: Registry port for provider extension defaults
            provider_registry: Registry for resolving provider-contributed defaults
        """
        self.config_manager = config_manager
        self.logger = logger
        self.template_factory = template_factory
        self.extension_registry = extension_registry
        self.provider_registry = provider_registry
        # Lazily-computed union of base + provider-subclass alias groups.
        self._alias_groups_cache: Optional[dict[str, tuple[str, ...]]] = None
        # Per-provider-type extension-config alias groups, computed on demand.
        self._extension_alias_groups_cache: dict[str, dict[str, tuple[str, ...]]] = {}

    def _effective_alias_groups(self) -> dict[str, tuple[str, ...]]:
        """Alias groups covering base ``Template`` plus every registered provider subclass.

        The base ``Template`` only declares its own ``AliasChoices`` fields;
        provider subclasses (e.g. ``AWSTemplate``) add top-level alias fields
        such as ``abis_instance_requirements`` / ``abisInstanceRequirements``.
        When a template factory is injected, those subclasses are discovered via
        the factory registry and their alias groups are unioned in, so no
        aliased field on any registered template model is missed during merges.
        Falls back to the static base-model groups when no factory is present.
        """
        if self._alias_groups_cache is not None:
            return self._alias_groups_cache

        group_maps: list[dict[str, tuple[str, ...]]] = [_FIELD_ALIAS_GROUPS]

        factory = self.template_factory
        getter = getattr(factory, "get_registered_template_classes", None)
        if callable(getter):
            try:
                registered = getter()
                classes = registered.values() if isinstance(registered, dict) else ()
                for tpl_cls in classes:
                    if isinstance(tpl_cls, type) and issubclass(tpl_cls, Template):
                        group_maps.append(_alias_groups_for_model(tpl_cls))
            except Exception as e:  # pragma: no cover — defensive
                self.logger.debug("Could not derive provider alias groups: %s", e)

        self._alias_groups_cache = (
            _union_alias_groups(group_maps) if len(group_maps) > 1 else _FIELD_ALIAS_GROUPS
        )
        return self._alias_groups_cache

    def _extension_alias_groups(self, provider_type: str) -> dict[str, tuple[str, ...]]:
        """Alias groups for a provider's *extension* config model.

        Extension configs (``ProviderTemplateExtensionBase`` subclasses such as
        ``K8sTemplateExtensionConfig``) declare their own ``AliasChoices`` fields
        — e.g. ``env`` / ``environment_variables`` — that neither the base
        ``Template`` nor the provider template subclasses carry.  The
        extension-layer merge must key by those groups so a higher-priority
        (instance) extension default under any alias drops the lower-priority
        (type) sibling.  Derived from the concrete extension class the registry
        holds for *provider_type*; empty when none is registered.
        """
        cached = self._extension_alias_groups_cache.get(provider_type)
        if cached is not None:
            return cached

        groups: dict[str, tuple[str, ...]] = {}
        registry = self.extension_registry
        getter = getattr(registry, "get_extension_class", None)
        if callable(getter):
            try:
                ext_cls = getter(provider_type)
                if isinstance(ext_cls, type) and issubclass(ext_cls, BaseModel):
                    groups = _alias_groups_for_model(ext_cls)
            except Exception as e:  # pragma: no cover — defensive
                self.logger.debug(
                    "Could not derive extension alias groups for %s: %s", provider_type, e
                )

        self._extension_alias_groups_cache[provider_type] = groups
        return groups

    def _template_and_extension_alias_groups(
        self, provider_type: Optional[str]
    ) -> dict[str, tuple[str, ...]]:
        """Union template-field and extension-config alias groups for a provider.

        Used when merging the extension-defaults layer against the resolved
        template dict, which mixes template-model aliases (e.g. ``machine_image``
        / ``image_id``) with extension-model aliases (e.g. ``env`` /
        ``environment_variables``).  Keying by the union ensures a template
        value under any alias drops the lower-priority extension sibling.
        """
        template_groups = self._effective_alias_groups()
        if not provider_type:
            return template_groups
        ext_groups = self._extension_alias_groups(provider_type)
        if not ext_groups:
            return template_groups
        return _union_alias_groups([template_groups, ext_groups])

    def _merge_layer(
        self,
        base: dict[str, Any],
        overlay: dict[str, Any],
        alias_groups: Optional[dict[str, tuple[str, ...]]] = None,
    ) -> dict[str, Any]:
        """Merge one default layer *overlay* onto *base* (overlay wins).

        Alias-aware: a field expressed under any alias in *overlay* supersedes
        the same field carried by *base* under a *different* alias, so at most
        one key per logical field survives — always the higher-priority
        (overlay) layer's value keyed under the overlay's alias.  Without this,
        both the canonical name and a legacy alias would survive a plain
        ``dict.update`` and the model's ``AliasChoices`` canonical-first
        precedence would silently pick the lower-priority layer's value.

        *alias_groups* selects the alias map to key by — the template-field
        groups by default, or the extension-config groups when merging
        extension layers.  Unlike :meth:`_coalesce_merge` this does not treat
        empty collections as unset — inter-layer defaults are authored
        configuration and an empty value there is intentional.
        """
        groups = alias_groups if alias_groups is not None else self._effective_alias_groups()
        result = dict(base)
        for key, value in overlay.items():
            result[key] = value
            for alias in groups.get(key, ()):
                if alias != key and alias in result:
                    del result[alias]
        return result

    def resolve_template_defaults(
        self,
        template_dict: dict[str, Any],
        provider_instance_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Apply hierarchical defaults to a template dictionary.

        Args:
            template_dict: Raw template data from file
            provider_name: Name of provider instance for context

        Returns:
            Template dictionary with defaults applied
        """
        self.logger.debug(
            "Resolving defaults for template %s",
            template_dict.get("template_id", "unknown"),
        )

        # Start with empty defaults
        resolved_defaults: dict[str, Any] = {}

        # 1. Apply global template defaults (lowest priority)
        global_defaults = self._get_global_template_defaults()
        resolved_defaults = self._merge_layer(resolved_defaults, global_defaults)
        self.logger.debug("Applied %s global defaults", len(global_defaults))

        # 2. Apply provider type defaults
        if provider_instance_name:
            provider_type = self._get_provider_type(provider_instance_name)
            if provider_type:
                provider_type_defaults = self._get_provider_type_defaults(provider_type)
                resolved_defaults = self._merge_layer(resolved_defaults, provider_type_defaults)
                self.logger.debug(
                    "Applied %s provider type defaults for %s",
                    len(provider_type_defaults),
                    provider_type,
                )

                # 3. Apply provider instance defaults
                provider_instance_defaults = self._get_provider_instance_defaults(
                    provider_instance_name
                )
                resolved_defaults = self._merge_layer(resolved_defaults, provider_instance_defaults)
                self.logger.debug(
                    "Applied %s provider instance defaults for %s",
                    len(provider_instance_defaults),
                    provider_instance_name,
                )

        # 4. Apply template values (highest priority - only for missing fields)
        # launch_template_id may be at top level (legacy) or inside provider_config (new path).
        _pc = template_dict.get("provider_config") or {}
        _has_lt = bool(
            template_dict.get("launch_template_id")
            or (_pc.get("launch_template_id") if isinstance(_pc, dict) else None)
        )
        if _has_lt:
            lt_fields = [
                k
                for k in (
                    "image_id",
                    "subnet_ids",
                    "security_group_ids",
                    "machine_types",
                    "machine_types_ondemand",
                    "machine_types_priority",
                )
                if k in resolved_defaults
            ]
            if lt_fields:
                self.logger.info(
                    "Template %s has launch_template_id set — suppressing %s from provider defaults",
                    template_dict.get("template_id", "unknown"),
                    lt_fields,
                )
        result = self._coalesce_merge(resolved_defaults, template_dict)

        self.logger.debug("Final template has %s fields after default resolution", len(result))

        # Belt-and-suspenders: warn if provider_api is still absent after all layers
        if not result.get("provider_api"):
            self.logger.warning(
                "provider_api is None after all default layers for template %s — "
                "no handler will be selected; set provider_api in the template file or provider defaults",
                result.get("template_id", "unknown"),
            )

        return result

    def _coalesce_merge(
        self, defaults: dict[str, Any], overrides: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Merge dictionaries with coalesce logic for empty collections.

        Empty list values in overrides are treated as "unset" so provider
        defaults can fill them in. An empty list is never a meaningful value
        for any template field, so this is safe to apply generically.

        Alias-aware: a template field may be expressed under either its
        canonical name (e.g. ``machine_image``) or a deprecated alias
        (e.g. ``image_id``).  When an override supplies a field under one
        alias while the defaults carry the *same* field under a different
        alias, the override must supersede the default — otherwise BOTH keys
        survive into the final dict and the domain model's ``AliasChoices``
        precedence silently picks the default over the caller's value.
        """
        result = defaults.copy()

        for key, value in overrides.items():
            if value is None:
                continue
            # Treat empty lists as "unset" — let the default win
            if self._is_empty_collection(value):
                continue
            result[key] = value
            # If this override targets an aliased field, drop any sibling
            # aliases carried by the defaults so the override actually wins.
            for alias in self._effective_alias_groups().get(key, ()):
                if alias != key and alias in result:
                    del result[alias]

        return result

    def _is_empty_collection(self, value: Any) -> bool:
        """Check if value is an empty collection (list, dict, set, tuple)."""
        return isinstance(value, (list, dict, set, tuple)) and len(value) == 0

    def resolve_provider_api_default(
        self,
        template_dict: dict[str, Any],
        provider_instance_name: Optional[str] = None,
    ) -> str:
        """
        Resolve provider_api default using hierarchical configuration.

        This method specifically handles the provider_api field which was
        previously hardcoded to 'aws' in the scheduler strategy.

        Args:
            template_dict: Template data
            provider_instance_name: Provider instance name for context

        Returns:
            Resolved provider_api value
        """
        # 1. Check template file first (highest priority)
        provider_api = template_dict.get("providerApi") or template_dict.get("provider_api")
        if provider_api:
            self.logger.debug("Using provider_api from template: %s", provider_api)
            return provider_api

        # 2. Check provider instance defaults
        if provider_instance_name:
            instance_defaults = self._get_provider_instance_defaults(provider_instance_name)
            if instance_defaults.get("provider_api"):
                provider_api = instance_defaults["provider_api"]
                self.logger.debug("Using provider_api from instance defaults: %s", provider_api)
                return provider_api

            # 3. Check provider type defaults
            provider_type = self._get_provider_type(provider_instance_name)
            if provider_type:
                type_defaults = self._get_provider_type_defaults(provider_type)
                if type_defaults.get("provider_api"):
                    provider_api = type_defaults["provider_api"]
                    self.logger.debug("Using provider_api from type defaults: %s", provider_api)
                    return provider_api

        # 4. Check global template defaults
        global_defaults = self._get_global_template_defaults()
        if global_defaults.get("provider_api"):
            provider_api = global_defaults["provider_api"]
            self.logger.debug("Using provider_api from global defaults: %s", provider_api)
            return provider_api

        # 5. No provider_api configured — raise to surface misconfiguration
        raise ValueError(
            "No provider_api configured — set provider_api in template file or provider defaults"
        )

    def get_effective_template_defaults(
        self, provider_instance_name: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Get the effective template defaults for a provider instance.

        This method returns the merged defaults without applying them to a specific template.
        Useful for validation and debugging.

        Args:
            provider_instance_name: Provider instance name

        Returns:
            Merged template defaults
        """
        defaults: dict[str, Any] = {}

        # Global defaults
        defaults = self._merge_layer(defaults, self._get_global_template_defaults())

        # Provider type defaults
        if provider_instance_name:
            provider_type = self._get_provider_type(provider_instance_name)
            if provider_type:
                defaults = self._merge_layer(
                    defaults, self._get_provider_type_defaults(provider_type)
                )

                # Provider instance defaults
                defaults = self._merge_layer(
                    defaults, self._get_provider_instance_defaults(provider_instance_name)
                )

        return defaults

    def _get_global_template_defaults(self) -> dict[str, Any]:
        """Get global template defaults from configuration."""
        try:
            template_config = self.config_manager.get_template_config()
            if hasattr(template_config, "model_dump"):
                config_dict = template_config.model_dump(exclude_none=True)  # type: ignore[union-attr]
            elif isinstance(template_config, dict):
                config_dict = template_config
            else:
                config_dict = {}

            # Extract only default-like fields from cleaned schema
            global_defaults = {}
            default_fields = [
                "max_number",
                "default_price_type",
                "default_provider_api",
            ]

            for field in default_fields:
                if field in config_dict and config_dict[field] is not None:
                    # Remove 'default_' prefix for clean field names
                    clean_field = (
                        field.replace("default_", "") if field.startswith("default_") else field
                    )
                    global_defaults[clean_field] = config_dict[field]

            return global_defaults

        except Exception as e:
            self.logger.warning("Could not get global template defaults: %s", e)
            return {}

    def _get_provider_type_defaults(self, provider_type: str) -> dict[str, Any]:
        """Get template defaults for a provider type."""
        try:
            provider_config = self.config_manager.get_provider_config()

            if hasattr(provider_config, "provider_defaults"):
                provider_defaults = provider_config.provider_defaults.get(provider_type)  # type: ignore[union-attr]
                if provider_defaults and hasattr(provider_defaults, "template_defaults"):
                    result = provider_defaults.template_defaults or {}
                    # Fallback: if no provider_api in template_defaults, delegate to the
                    # provider registry which reads it from the provider's registration.
                    if not result.get("provider_api") and self.provider_registry is not None:
                        default_api = self.provider_registry.get_default_api(provider_type)
                        if default_api:
                            result = dict(result)
                            result["provider_api"] = default_api
                    return result

            return {}

        except Exception as e:
            self.logger.warning("Could not get provider type defaults for %s: %s", provider_type, e)
            return {}

    def _get_provider_instance_defaults(self, provider_instance_name: str) -> dict[str, Any]:
        """Get template defaults for a specific provider instance."""
        try:
            provider_config = self.config_manager.get_provider_config()

            if hasattr(provider_config, "providers"):
                for provider in provider_config.providers:  # type: ignore[union-attr]
                    if provider.name == provider_instance_name:
                        return provider.template_defaults or {}

            return {}

        except Exception as e:
            self.logger.warning(
                "Could not get provider instance defaults for %s: %s",
                provider_instance_name,
                e,
            )
            return {}

    def _get_provider_type(self, provider_instance_name: str) -> Optional[str]:
        """Get provider type from provider instance name."""
        try:
            provider_config = self.config_manager.get_provider_config()

            if hasattr(provider_config, "providers"):
                for provider in provider_config.providers:  # type: ignore[union-attr]
                    if provider.name == provider_instance_name:
                        return provider.type

            # Fallback: extract from name (e.g., 'aws-primary' -> 'aws')
            return extract_provider_type(provider_instance_name)

        except Exception as e:
            self.logger.warning(
                "Could not determine provider type for %s: %s",
                provider_instance_name,
                e,
            )
            return None

    def validate_template_defaults(
        self, provider_instance_name: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Validate template defaults configuration.

        Args:
            provider_instance_name: Provider instance to validate

        Returns:
            Validation results with any issues found
        """
        validation_result = {
            "is_valid": True,
            "warnings": [],
            "errors": [],
            "provider_instance": provider_instance_name,
        }

        try:
            # Check if defaults are correctly configured
            effective_defaults = self.get_effective_template_defaults(provider_instance_name)

            # Validate essential fields have defaults
            essential_fields = ["provider_api", "price_type", "max_number"]
            for field in essential_fields:
                if field not in effective_defaults:
                    validation_result["warnings"].append(
                        f"No default configured for essential field: {field}"
                    )

            self.logger.info(
                "Template defaults validation completed for %s",
                provider_instance_name or "global",
            )

        except Exception as e:
            validation_result["is_valid"] = False
            validation_result["errors"].append(f"Validation failed: {e!s}")
            self.logger.error("Template defaults validation failed: %s", e)

        return validation_result

    def resolve_template_with_extensions(
        self,
        template_dict: dict[str, Any],
        provider_instance_name: Optional[str] = None,
    ) -> Template:
        """
        Resolve template with provider extensions using domain factory.

        This method integrates hierarchical defaults
        with domain extensions and creates appropriate domain template objects.

        Args:
            template_dict: Raw template data from file
            provider_instance_name: Provider instance name for context

        Returns:
            Domain template object with extensions applied
        """
        self.logger.debug(
            "Resolving template with extensions: %s",
            template_dict.get("template_id", "unknown"),
        )

        # 1. Apply hierarchical defaults (existing logic)
        resolved_dict = self.resolve_template_defaults(template_dict, provider_instance_name)

        # 2. Determine provider type
        provider_type = (
            self._get_provider_type(provider_instance_name) if provider_instance_name else None
        )

        # 3. Apply provider extension defaults
        if provider_type:
            extension_defaults = self._get_extension_defaults(provider_type, provider_instance_name)
            # Extension defaults have lower priority than hierarchical defaults.
            # Alias-aware across BOTH template-field and extension-field aliases
            # so a resolved template value under any alias drops the sibling
            # alias contributed by the lower-priority extension layer.
            resolved_dict = self._merge_layer(
                extension_defaults,
                resolved_dict,
                self._template_and_extension_alias_groups(provider_type),
            )
            self.logger.debug(
                "Applied %s extension defaults for %s",
                len(extension_defaults),
                provider_type,
            )

        # 4. Create appropriate template type via factory
        if self.template_factory:
            try:
                template = self.template_factory.create_template(resolved_dict, provider_type)
                self.logger.debug("Created %s via factory", type(template).__name__)
                return template
            except Exception as e:
                self.logger.error("Failed to create template via factory: %s", e)
                # Fall back to core template

        # Fallback: create core template directly
        try:
            template = Template(**resolved_dict)
            self.logger.debug("Created core Template as fallback")
            return template
        except Exception as e:
            self.logger.error("Failed to create core template: %s", e)
            raise

    def _get_extension_defaults(
        self, provider_type: str, provider_instance_name: Optional[str]
    ) -> dict[str, Any]:
        """
        Get provider extension defaults with hierarchy.

        Args:
            provider_type: Provider type (e.g., 'aws', 'provider1')
            provider_instance_name: Provider instance name for overrides

        Returns:
            Dictionary of extension defaults
        """
        extension_defaults: dict[str, Any] = {}

        if self.extension_registry is None:
            return extension_defaults

        # Alias groups for this provider's extension model so the type->instance
        # extension layering is alias-aware (e.g. env / environment_variables).
        ext_alias_groups = self._extension_alias_groups(provider_type)

        try:
            # 1. Get provider type extension defaults — safe for unknown providers (returns {})
            type_extension_defaults = self.extension_registry.get_extension_defaults(provider_type)
            extension_defaults = self._merge_layer(
                extension_defaults, type_extension_defaults, ext_alias_groups
            )
            self.logger.debug("Applied %s type extension defaults", len(type_extension_defaults))

            # 2. Get provider instance extension overrides
            if provider_instance_name:
                instance_extension_defaults = self._get_provider_instance_extension_defaults(
                    provider_instance_name, provider_type
                )
                extension_defaults = self._merge_layer(
                    extension_defaults, instance_extension_defaults, ext_alias_groups
                )
                self.logger.debug(
                    "Applied %s instance extension defaults",
                    len(instance_extension_defaults),
                )

        except Exception as e:
            self.logger.warning("Could not load extension defaults for %s: %s", provider_type, e)

        return extension_defaults

    def _get_provider_instance_extension_defaults(
        self, provider_instance_name: str, provider_type: str
    ) -> dict[str, Any]:
        """
        Get extension defaults for a specific provider instance.

        Args:
            provider_instance_name: Provider instance name
            provider_type: Provider type for extension lookup

        Returns:
            Dictionary of instance-specific extension defaults
        """
        try:
            provider_config = self.config_manager.get_provider_config()

            if hasattr(provider_config, "providers"):
                for provider in provider_config.providers:  # type: ignore[union-attr]
                    if (
                        provider.name == provider_instance_name
                        and hasattr(provider, "extensions")
                        and provider.extensions
                    ):
                        if self.extension_registry is not None:
                            # Delegate to extension registry — returns {} for unknown providers,
                            # so this is safe to call unconditionally.
                            result = self.extension_registry.get_extension_defaults(
                                provider_type, provider.extensions
                            )
                            # Fall back to raw extensions dict when registry has no entry.
                            return result if result else provider.extensions
                        return provider.extensions

            return {}

        except Exception as e:
            self.logger.warning(
                "Could not get instance extension defaults for %s: %s",
                provider_instance_name,
                e,
            )
            return {}

    def get_effective_template_with_extensions(
        self,
        template_dict: dict[str, Any],
        provider_instance_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Get effective template configuration with all defaults and extensions applied.

        This method is useful for debugging and validation - it shows the final
        resolved configuration without creating a domain object.

        Args:
            template_dict: Raw template data
            provider_instance_name: Provider instance name

        Returns:
            Dictionary with all defaults and extensions applied
        """
        # Apply hierarchical defaults
        resolved_dict = self.resolve_template_defaults(template_dict, provider_instance_name)

        # Apply extension defaults
        provider_type = (
            self._get_provider_type(provider_instance_name) if provider_instance_name else None
        )
        if provider_type:
            extension_defaults = self._get_extension_defaults(provider_type, provider_instance_name)
            # Alias-aware extension->template merge (see resolve_template_with_extensions).
            resolved_dict = self._merge_layer(
                extension_defaults,
                resolved_dict,
                self._template_and_extension_alias_groups(provider_type),
            )

        return resolved_dict

    def validate_template_with_extensions(
        self,
        template_dict: dict[str, Any],
        provider_instance_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Validate template configuration with extensions.

        Args:
            template_dict: Template data to validate
            provider_instance_name: Provider instance name

        Returns:
            Validation results including extension validation
        """
        validation_result = self.validate_template_defaults(provider_instance_name)

        try:
            # Try to create template with extensions to validate
            template = self.resolve_template_with_extensions(template_dict, provider_instance_name)

            # Additional validation for domain template
            if hasattr(template, "model_validate"):
                try:
                    template.model_validate(template.model_dump())
                    validation_result["domain_validation"] = "passed"
                except Exception as e:
                    validation_result["warnings"].append(f"Domain validation failed: {e}")
                    validation_result["domain_validation"] = "failed"

        except Exception as e:
            validation_result["errors"].append(f"Template creation with extensions failed: {e}")
            validation_result["is_valid"] = False

        return validation_result
