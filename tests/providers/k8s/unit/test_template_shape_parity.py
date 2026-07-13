"""Drift-guard: assert that the 5 k8s template shapes stay in sync.

The kubernetes template field surface is defined in 5 places that must
agree.  This test suite locks down the invariants so any future
divergence is caught at test time rather than at operator runtime.

The 5 shapes:
  S1  K8sTemplate            domain source of truth       (k8s_template.py)
  S2  K8sTemplateDTOConfig    DTO wire config              (k8s_template_dto_config.py)
  S3  K8sTemplateExtensionConfig  operator defaults        (template_extension.py)
  S4  _SUPPORTED_FIELDS       adapter introspection list   (template_adapter.py)
  S5  K8sFieldMapping targets  HF->internal field map      (hostfactory_field_mapping.py)

Documented exceptions (asserted explicitly, not silently skipped):
  namespaces   — multi-namespace scheduling list; absent from S2/S3/S4 because
                 it is set directly from the HF payload, not through the DTO
                 config pipeline.
  native_spec  — full-replacement escape hatch in S1/S2; absent from S3/S4/S5
                 because it bypasses the spec builders and has no HF key.
  args/command/pod_spec_override
               — present in S1/S2/S4/S5 but intentionally absent from S3
                 (K8sTemplateExtensionConfig) because provider-level defaults
                 for these low-level overrides are not meaningful.
"""

from __future__ import annotations

from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.template_extension import K8sTemplateExtensionConfig
from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
from orb.providers.k8s.domain.template.k8s_template_dto_config import K8sTemplateDTOConfig
from orb.providers.k8s.infrastructure.adapters.template_adapter import _SUPPORTED_FIELDS
from orb.providers.k8s.scheduler.hostfactory_field_mapping import K8sFieldMapping

# ---------------------------------------------------------------------------
# Derive the canonical domain field set once
# ---------------------------------------------------------------------------

_PARENT_FIELDS: frozenset[str] = frozenset(Template.model_fields.keys())
_DOMAIN_K8S_ALL: frozenset[str] = frozenset(K8sTemplate.model_fields.keys()) - _PARENT_FIELDS

# Fields excluded from the "operator-facing" surface with documented reasons.
# Each exclusion is tested explicitly in the assertions below.
_INTERNAL_ONLY = frozenset({"provider_config"})  # internal round-trip carrier
# Fields that are escape hatches bypassing the typed spec builders.
# ``native_spec`` has no HostFactory key; ``native_spec_path`` has an HF key
# (``nativeSpecPath``) but is treated the same way — it bypasses spec builders
# and is therefore excluded from the operator-facing parity obligations.
_NO_HF_KEY = frozenset({"native_spec", "native_spec_path"})
_SCHEDULING_LIST = frozenset({"namespaces"})  # multi-namespace; not in DTO pipeline

# The "normal" operator-facing k8s fields that all shapes should know about.
_DOMAIN_K8S_OPERATOR = _DOMAIN_K8S_ALL - _INTERNAL_ONLY - _NO_HF_KEY - _SCHEDULING_LIST

# Fields S3 (ExtensionConfig) intentionally omits: low-level overrides that
# don't make sense as provider-level defaults.  service_name is a per-template
# StatefulSet governing-Service name, not a provider-wide default.
# native_spec_path is the file-path companion to native_spec — neither makes
# sense as a provider-wide default.
_S3_INTENTIONAL_OMISSIONS = frozenset(
    {"args", "command", "native_spec_path", "pod_spec_override", "service_name"}
)


# ---------------------------------------------------------------------------
# (a) S2 (DTOConfig) covers all operator-facing domain fields
# ---------------------------------------------------------------------------


def test_s2_dto_config_covers_operator_domain_fields() -> None:
    """K8sTemplateDTOConfig (S2) must declare every operator-facing domain field.

    Exception: ``namespaces`` is set directly from the HF payload and is not
    part of the DTO pipeline.  ``native_spec`` is an escape hatch with no HF
    equivalent.  Both are asserted absent from S2 here to make the intent
    explicit.
    """
    s2_fields = frozenset(K8sTemplateDTOConfig.model_fields.keys())

    # S2 must cover every operator-facing domain field.
    missing = _DOMAIN_K8S_OPERATOR - s2_fields
    assert not missing, (
        f"K8sTemplateDTOConfig is missing domain fields: {sorted(missing)}.  "
        "Add them to S2 or add documented exceptions to this test."
    )

    # Documented exceptions must remain absent from S2.
    assert "namespaces" not in s2_fields, (
        "namespaces should NOT be in K8sTemplateDTOConfig — it is set directly "
        "from the HF payload and must not go through the DTO pipeline."
    )
    # native_spec IS intentionally present in S2 as an escape hatch.
    assert "native_spec" in s2_fields, (
        "native_spec should be present in K8sTemplateDTOConfig — it is the "
        "full-replacement escape hatch that must be round-trippable through the DTO."
    )
    # native_spec_path IS intentionally present in S2 — file-path companion
    # to native_spec that must be round-trippable through the DTO.
    assert "native_spec_path" in s2_fields, (
        "native_spec_path should be present in K8sTemplateDTOConfig — it is the "
        "file-path companion to native_spec that must be round-trippable through the DTO."
    )


# ---------------------------------------------------------------------------
# (b) S3 (ExtensionConfig) is a strict subset of S2 (DTOConfig)
# ---------------------------------------------------------------------------


def test_s3_extension_config_is_subset_of_s2_dto_config() -> None:
    """K8sTemplateExtensionConfig (S3) must only contain fields that S2 also has.

    S3 is a subset of S2: every field in the operator-defaults layer must also
    be representable in the DTO wire config.  The reverse is not required —
    S2 intentionally includes fields (``native_spec``, ``args``, ``command``,
    ``pod_spec_override``) that are not meaningful as provider-level defaults.
    """
    s2_fields = frozenset(K8sTemplateDTOConfig.model_fields.keys())
    s3_fields = frozenset(K8sTemplateExtensionConfig.model_fields.keys())

    not_in_s2 = s3_fields - s2_fields
    assert not not_in_s2, (
        f"K8sTemplateExtensionConfig has fields not in K8sTemplateDTOConfig: "
        f"{sorted(not_in_s2)}.  Either add them to S2 or remove them from S3."
    )

    # Confirm S3 omissions are the expected documented set.
    s3_omits_from_s2 = (s2_fields - s3_fields) - frozenset({"native_spec"})
    assert s3_omits_from_s2 == _S3_INTENTIONAL_OMISSIONS, (
        f"K8sTemplateExtensionConfig omits unexpected fields from K8sTemplateDTOConfig.\n"
        f"  Expected omissions: {sorted(_S3_INTENTIONAL_OMISSIONS)}\n"
        f"  Actual omissions:   {sorted(s3_omits_from_s2)}\n"
        "Update _S3_INTENTIONAL_OMISSIONS in this test if the omission set changes."
    )


# ---------------------------------------------------------------------------
# (c) S4 (_SUPPORTED_FIELDS) covers operator-facing domain fields
# ---------------------------------------------------------------------------


def test_s4_supported_fields_covers_operator_domain_fields() -> None:
    """_SUPPORTED_FIELDS (S4) must cover every operator-facing domain field.

    ``namespaces`` is excluded because it has no HostFactory key and is not
    part of the operator-template surface.  ``native_spec`` is an escape hatch
    excluded from S4 by design (it bypasses the spec builders entirely).
    Both are asserted absent to make the intent explicit.
    """
    s4_set = frozenset(_SUPPORTED_FIELDS)

    missing = _DOMAIN_K8S_OPERATOR - s4_set
    assert not missing, (
        f"_SUPPORTED_FIELDS is missing operator domain fields: {sorted(missing)}.  "
        "Add them to _compute_supported_fields or add documented exceptions."
    )

    assert "namespaces" not in s4_set, (
        "namespaces must NOT be in _SUPPORTED_FIELDS — it has no HF mapping key."
    )
    assert "native_spec" not in s4_set, (
        "native_spec must NOT be in _SUPPORTED_FIELDS — it is an escape hatch "
        "that bypasses the spec builders."
    )


# ---------------------------------------------------------------------------
# (d) S5 (FieldMapping targets) covers operator-facing domain fields
# ---------------------------------------------------------------------------


def test_s5_field_mapping_targets_cover_operator_domain_fields() -> None:
    """K8sFieldMapping (S5) targets must cover every operator-facing domain field.

    ``native_spec`` has no HF key and is excluded from S5 by design.
    ``namespaces`` is covered (``namespaces`` -> ``namespaces`` mapping exists)
    so it is not in the exclusion set here.
    """
    fm_targets = frozenset(K8sFieldMapping._PROVIDER_MAPPINGS.values())

    missing = _DOMAIN_K8S_OPERATOR - fm_targets
    assert not missing, (
        f"K8sFieldMapping targets are missing operator domain fields: {sorted(missing)}.  "
        "Add a mapping entry to K8sFieldMapping._PROVIDER_MAPPINGS."
    )

    # native_spec has no HF key — confirm it is absent from mapping targets.
    assert "native_spec" not in fm_targets, (
        "native_spec must NOT appear as a FieldMapping target — it is an escape "
        "hatch with no HostFactory equivalent."
    )


# ---------------------------------------------------------------------------
# (e) No ``environment_variables`` spelling in domain or S4
# ---------------------------------------------------------------------------


def test_environment_variables_spelling_absent_from_domain_and_s4() -> None:
    """The old ``environment_variables`` field name must not exist in domain or S4.

    The canonical field name is ``env`` on K8sTemplate (S1).  Back-compat for
    the old spelling is provided via AliasChoices on S2 and S3 — operators who
    still use ``environment_variables`` in their YAML continue to be served, but
    the domain never exposes that name.
    """
    # Domain must not have environment_variables as a first-class field.
    assert "environment_variables" not in K8sTemplate.model_fields, (
        "K8sTemplate.environment_variables must not exist — use K8sTemplate.env instead."
    )

    # S4 must use the canonical name 'env'.
    assert "environment_variables" not in _SUPPORTED_FIELDS, (
        "_SUPPORTED_FIELDS must not contain 'environment_variables' — use 'env'."
    )
    assert "env" in _SUPPORTED_FIELDS, (
        "_SUPPORTED_FIELDS must contain 'env' (the canonical field name)."
    )

    # S2 and S3 must use 'env' as the field name (alias is separate).
    assert "environment_variables" not in K8sTemplateDTOConfig.model_fields, (
        "K8sTemplateDTOConfig.environment_variables must not exist as a field — "
        "use K8sTemplateDTOConfig.env with AliasChoices for back-compat."
    )
    assert "env" in K8sTemplateDTOConfig.model_fields, (
        "K8sTemplateDTOConfig must have an 'env' field."
    )
    assert "environment_variables" not in K8sTemplateExtensionConfig.model_fields, (
        "K8sTemplateExtensionConfig.environment_variables must not exist as a field — "
        "use K8sTemplateExtensionConfig.env with AliasChoices for back-compat."
    )
    assert "env" in K8sTemplateExtensionConfig.model_fields, (
        "K8sTemplateExtensionConfig must have an 'env' field."
    )
