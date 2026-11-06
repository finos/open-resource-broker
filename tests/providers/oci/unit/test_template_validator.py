"""OCITemplateValidator accepts every shipped template and reports real errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import orb.providers.oci as _oci_pkg
from orb.providers.oci.domain.template import OCITemplate
from orb.providers.oci.validation.template_validator import OCITemplateValidator

_SHIPPED = json.loads(
    (Path(_oci_pkg.__file__).parent / "config" / "oci_templates.json").read_text()
)["templates"]


def _base(**extra) -> dict:
    return {
        "template_id": "t1",
        "provider_type": "oci",
        "provider_api": "OCICompute",
        "machine_types": {"VM.Standard.E6.Flex": 1},
        **extra,
    }


@pytest.mark.parametrize("row", _SHIPPED, ids=lambda r: r["template_id"])
def test_shipped_templates_have_no_errors(row: dict) -> None:
    validator = OCITemplateValidator()

    assert validator.validate(row).errors == []
    # The same template after a model round trip, as the update path feeds it.
    model = OCITemplate.model_validate(row)
    assert validator.validate(model.model_dump()).errors == []
    assert validator.validate(model).errors == []


def test_unknown_capacity_type_is_an_error() -> None:
    result = OCITemplateValidator().validate(_base(capacity_type="spot"))

    assert not result.valid
    assert any("capacity_type" in e for e in result.errors)


@pytest.mark.parametrize("field", ["ocpus", "memory_gbs", "boot_volume_gbs"])
def test_non_positive_numbers_are_errors(field: str) -> None:
    result = OCITemplateValidator().validate(_base(**{field: 0}))

    assert any(field in e for e in result.errors)


def test_missing_shape_is_only_a_warning() -> None:
    row = _base()
    del row["machine_types"]

    result = OCITemplateValidator().validate(row)

    assert result.valid
    assert result.warnings


def test_unexpected_failure_is_logged_not_silent(monkeypatch) -> None:
    from orb.providers.oci.validation import template_validator as mod

    messages: list[str] = []
    monkeypatch.setattr(mod._logger, "warning", lambda msg, *a: messages.append(msg % a))

    result = OCITemplateValidator().validate(object())

    assert result.valid
    assert any("could not run" in m for m in messages)
