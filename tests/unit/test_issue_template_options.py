"""Guard: issue templates and labels stay in sync with the real registries.

ORB registers providers, schedulers and storage backends dynamically through
the strategy/registry pattern — there is no static list anywhere in the code
of "every provider" or "every scheduler". The provider, scheduler, storage
and interface options in ``.github/ISSUE_TEMPLATE/bug_report.yml`` and
``feature_request.yml``, and the ``area:`` labels in ``.github/labels.yml``,
are hand-written strings that nothing forces to be updated when a new type
is registered. These tests run the same discovery/registration functions
ORB's own startup uses, read the resulting registries, and fail loudly, with
the exact file and option to add, whenever a template or the label taxonomy
falls behind.

Sources used (no hardcoded id -> display-name tables in this file):
- Providers: ``orb.providers.registration.register_all_providers`` populates
  the live :class:`~orb.providers.registry.ProviderRegistry` exactly as
  startup does (entry-point discovery, then each plugin's
  ``register_provider()``). Display names come from
  :meth:`ProviderRegistry.get_display_name`, which reads the ``display_name``
  each provider plugin declares (see
  :meth:`orb.providers.base.provider_plugin.ProviderPlugin.display_name`).
- Schedulers: ``orb.infrastructure.scheduler.registration.register_all_scheduler_types``
  populates the live ``SchedulerRegistry``; display names and the list of
  third-party integrations that ride on a given scheduler (HTC-Grid,
  OpenGRIS Scaler, on the ``default`` scheduler) come from
  ``SchedulerRegistry.get_display_metadata`` / ``get_integrations``.
- Storage backends: ``orb.infrastructure.storage.registration.register_all_storage_types``
  plus the AWS-provided backends' own registration functions populate the
  live ``StorageRegistry``; display names come from
  ``StorageRegistry.get_display_name``.
- SDKs: directories under ``sdk/`` that contain a recognised package manifest
  (``go.mod``, ``package.json``, ``build.gradle(.kts)``, ``*.csproj``).
  Display names come from ``sdk/display_names.yml``, a mapping that lives
  next to the SDKs themselves rather than in this test. The Python SDK is
  ORB's own package rather than a directory under ``sdk/``, so it is a fixed
  constant here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
SDK_DIR = REPO_ROOT / "sdk"
SDK_DISPLAY_NAMES_FILE = SDK_DIR / "display_names.yml"
BUG_REPORT = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
FEATURE_REQUEST = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "feature_request.yml"
LABELS_FILE = REPO_ROOT / ".github" / "labels.yml"

# The Python SDK is ORB's own package (pyproject.toml), not a directory under
# sdk/, so there is no registry entry to derive it from.
PYTHON_SDK_DISPLAY_NAME = "Python SDK"

# Filename patterns (searched recursively) that mark a directory under sdk/
# as a real, maintained SDK package rather than a spec or parity-test harness.
SDK_MANIFEST_GLOBS: tuple[str, ...] = (
    "go.mod",
    "package.json",
    "build.gradle",
    "build.gradle.kts",
    "*.csproj",
)


# ---------------------------------------------------------------------------
# Registry access — these call the same registration functions ORB's own
# startup path uses, so the resulting ids and display names are always real.
# ---------------------------------------------------------------------------


def _registered_providers() -> dict[str, str]:
    """Return {provider_id: display_name} after running real provider registration."""
    from orb.providers.registration import register_all_providers
    from orb.providers.registry import get_provider_registry

    register_all_providers(container=None)
    registry = get_provider_registry()
    return {
        provider_id: registry.get_display_name(provider_id)
        for provider_id in sorted(registry.get_registered_providers())
    }


def _registered_schedulers() -> dict[str, str]:
    """Return {scheduler_id: display_name} after running real scheduler registration."""
    from orb.infrastructure.scheduler.registration import register_all_scheduler_types
    from orb.infrastructure.scheduler.registry import get_scheduler_registry

    register_all_scheduler_types()
    registry = get_scheduler_registry()
    return {
        scheduler_id: registry.get_display_metadata(scheduler_id)["display_name"]
        for scheduler_id in sorted(registry.get_registered_types())
    }


def _scheduler_integration_options() -> list[str]:
    """Return dropdown-style option strings for third-party scheduler integrations.

    Reads the integrations declared on each registered scheduler (for
    example HTC-Grid and OpenGRIS Scaler on the ``default`` scheduler) rather
    than hardcoding the list, so a newly declared integration is picked up
    automatically.
    """
    from orb.infrastructure.scheduler.registration import register_all_scheduler_types
    from orb.infrastructure.scheduler.registry import get_scheduler_registry

    register_all_scheduler_types()
    registry = get_scheduler_registry()
    options = []
    for scheduler_id in sorted(registry.get_registered_types()):
        for integration in registry.get_integrations(scheduler_id):
            options.append(f"{integration} (via {scheduler_id} scheduler)")
    return options


def _registered_storage_backends() -> dict[str, str]:
    """Return {storage_id: display_name} after running real storage registration."""
    from orb.infrastructure.storage.registration import register_all_storage_types
    from orb.infrastructure.storage.registry import get_storage_registry
    from orb.providers.aws.storage.registration import (
        register_aurora_storage,
        register_dynamodb_storage,
    )

    register_all_storage_types()
    # DynamoDB/Aurora are AWS-provided backends, normally wired in through the
    # AWS provider plugin's post-init hook; call their registration functions
    # directly so this test doesn't need a full DI container to see them.
    register_dynamodb_storage()
    register_aurora_storage()

    registry = get_storage_registry()
    return {
        storage_id: registry.get_display_name(storage_id)
        for storage_id in sorted(registry.get_registered_storage_types())
    }


def _sdk_display_names() -> dict[str, str]:
    return yaml.safe_load(SDK_DISPLAY_NAMES_FILE.read_text(encoding="utf-8"))


def _has_manifest(directory: Path) -> bool:
    return any(any(directory.rglob(pattern)) for pattern in SDK_MANIFEST_GLOBS)


def _sdk_ids() -> list[str]:
    if not SDK_DIR.is_dir():
        return []
    return sorted(
        child.name for child in SDK_DIR.iterdir() if child.is_dir() and _has_manifest(child)
    )


def _registered_sdks() -> dict[str, str]:
    """Return {sdk_id: display_name} for every sdk/ directory with a manifest."""
    display_names = _sdk_display_names()
    sdk_ids = _sdk_ids()
    for sdk_id in sdk_ids:
        assert sdk_id in display_names, (
            f"'sdk/{sdk_id}' contains a package manifest but has no display name. "
            f"Add '{sdk_id}: <Display Name>' to sdk/display_names.yml."
        )
    return {sdk_id: display_names[sdk_id] for sdk_id in sdk_ids}


# ---------------------------------------------------------------------------
# Template / label parsing helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _dropdown_options(doc: dict, field_id: str) -> list[str]:
    """Return the ``options`` list for the dropdown field with the given id."""
    for item in doc.get("body", []):
        if item.get("type") == "dropdown" and item.get("id") == field_id:
            return list(item["attributes"]["options"])
    raise AssertionError(f"No dropdown field with id={field_id!r} found")


def _label_names() -> set[str]:
    labels = yaml.safe_load(LABELS_FILE.read_text(encoding="utf-8"))
    return {label["name"] for label in labels}


def _assert_all_covered(items: dict[str, str], options: list[str], where: str) -> None:
    """Assert every display name in *items* appears in *options*.

    *where* names the file/dropdown in the failure message so a contributor
    knows exactly what to edit.
    """
    for item_id, display_name in items.items():
        assert display_name in options, (
            f"'{display_name}' (id={item_id!r}) is missing from {where}. "
            f'Add the option "{display_name}".'
        )


def _assert_labels_cover(items: dict[str, str], label_prefix: str, label_names: set[str]) -> None:
    """Assert every id in *items* has an ``area: <label_prefix>/<id>`` label."""
    for item_id in items:
        label = f"area: {label_prefix}/{item_id}"
        assert label in label_names, (
            f"'{item_id}' has no matching label. Add:\n- name: \"{label}\"\nto .github/labels.yml."
        )


def _mentioned_in(text: str, display_name: str) -> bool:
    """Return True if *display_name*, or its natural short form, appears in *text*.

    Handles the naming patterns used in README.md's prose, which favours
    short product names over official full names:
    - "Amazon Web Services (AWS)" — the acronym inside the parentheses is
      what the prose actually uses.
    - "IBM Spectrum Symphony (host factory)" — the name before the
      parentheses is what the prose actually uses.
    - "Microsoft Azure" — the last word (the product name without the
      corporate prefix) is what the prose actually uses.
    Falls back to a verbatim check for display names that already match
    README's style as-is (e.g. "Kubernetes", "Slurm Workload Manager").
    """
    if display_name in text:
        return True
    match = re.match(r"^(.*?)\s*\(([^)]+)\)$", display_name)
    if match:
        before, inside = match.group(1).strip(), match.group(2).strip()
        if (before and before in text) or (inside and inside in text):
            return True
    last_word = display_name.rsplit(" ", 1)[-1]
    return bool(last_word) and last_word in text


# ---------------------------------------------------------------------------
# bug_report.yml dropdown coverage
# ---------------------------------------------------------------------------


def test_bug_report_provider_dropdown_lists_every_provider() -> None:
    options = _dropdown_options(_load_yaml(BUG_REPORT), "provider")
    _assert_all_covered(
        _registered_providers(), options, "the 'Provider' dropdown in bug_report.yml"
    )


def test_bug_report_scheduler_dropdown_lists_every_scheduler() -> None:
    options = _dropdown_options(_load_yaml(BUG_REPORT), "scheduler")
    _assert_all_covered(
        _registered_schedulers(), options, "the 'Scheduler' dropdown in bug_report.yml"
    )
    for integration_option in _scheduler_integration_options():
        assert integration_option in options, (
            f"'{integration_option}' is missing from the 'Scheduler' dropdown in "
            f'bug_report.yml. Add the option "{integration_option}".'
        )


def test_bug_report_interface_dropdown_lists_every_sdk() -> None:
    options = _dropdown_options(_load_yaml(BUG_REPORT), "interface")
    assert PYTHON_SDK_DISPLAY_NAME in options, (
        f"'{PYTHON_SDK_DISPLAY_NAME}' is missing from the 'Interface' dropdown in bug_report.yml."
    )
    _assert_all_covered(_registered_sdks(), options, "the 'Interface' dropdown in bug_report.yml")


def test_bug_report_storage_dropdown_lists_every_storage_backend() -> None:
    options = _dropdown_options(_load_yaml(BUG_REPORT), "storage")
    _assert_all_covered(
        _registered_storage_backends(),
        options,
        "the 'Storage backend' dropdown in bug_report.yml",
    )


# ---------------------------------------------------------------------------
# feature_request.yml Area dropdown coverage
# ---------------------------------------------------------------------------


def test_feature_request_area_dropdown_lists_every_option() -> None:
    options = _dropdown_options(_load_yaml(FEATURE_REQUEST), "area")

    _assert_all_covered(
        _registered_providers(), options, "the 'Area' dropdown in feature_request.yml"
    )
    _assert_all_covered(
        _registered_schedulers(), options, "the 'Area' dropdown in feature_request.yml"
    )
    for integration_option in _scheduler_integration_options():
        assert integration_option in options, (
            f"'{integration_option}' is missing from the 'Area' dropdown in "
            f'feature_request.yml. Add the option "{integration_option}".'
        )

    assert PYTHON_SDK_DISPLAY_NAME in options, (
        f"'{PYTHON_SDK_DISPLAY_NAME}' is missing from the 'Area' dropdown in feature_request.yml."
    )
    _assert_all_covered(_registered_sdks(), options, "the 'Area' dropdown in feature_request.yml")


# ---------------------------------------------------------------------------
# README coverage
# ---------------------------------------------------------------------------


def test_readme_mentions_every_provider() -> None:
    readme_text = README.read_text(encoding="utf-8")
    for provider_id, display_name in _registered_providers().items():
        assert _mentioned_in(readme_text, display_name), (
            f"README.md does not mention provider '{display_name}' (id={provider_id!r}). "
            "Add it to the provider support list in README.md."
        )


def test_readme_mentions_every_scheduler() -> None:
    readme_text = README.read_text(encoding="utf-8")
    for scheduler_id, display_name in _registered_schedulers().items():
        assert _mentioned_in(readme_text, display_name), (
            f"README.md does not mention scheduler '{display_name}' (id={scheduler_id!r}). "
            "Add it to the scheduler support list in README.md."
        )
    for integration_option in _scheduler_integration_options():
        integration_name = integration_option.split(" (via ")[0]
        assert integration_name in readme_text, (
            f"README.md does not mention scheduler integration '{integration_name}'. "
            "Add it to the scheduler support list in README.md."
        )


# ---------------------------------------------------------------------------
# labels.yml coverage
# ---------------------------------------------------------------------------


def test_labels_cover_every_provider() -> None:
    _assert_labels_cover(_registered_providers(), "providers", _label_names())


def test_labels_cover_every_scheduler() -> None:
    label_names = _label_names()
    assert "area: scheduler" in label_names, (
        'The generic "area: scheduler" label is missing. Add:\n'
        '- name: "area: scheduler"\n'
        "to .github/labels.yml."
    )
    _assert_labels_cover(_registered_schedulers(), "scheduler", label_names)


def test_labels_cover_every_storage_backend() -> None:
    _assert_labels_cover(_registered_storage_backends(), "storage", _label_names())


# ---------------------------------------------------------------------------
# Proof that the coverage checks actually catch drift: register a type the
# templates/labels don't know about and confirm the shared assertion helper
# fails, naming it. Cleans up the dummy registration either way.
# ---------------------------------------------------------------------------


def test_provider_coverage_check_fails_for_an_unlisted_provider() -> None:
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()
    registry.register_provider(
        provider_type="dummycloud",
        strategy_factory=lambda *_: None,
        config_factory=lambda *_: {},
    )
    try:
        providers = _registered_providers()
        assert "dummycloud" in providers

        options = _dropdown_options(_load_yaml(BUG_REPORT), "provider")
        with pytest.raises(AssertionError, match="dummycloud"):
            _assert_all_covered(providers, options, "the 'Provider' dropdown in bug_report.yml")
    finally:
        registry.unregister_type("dummycloud")
