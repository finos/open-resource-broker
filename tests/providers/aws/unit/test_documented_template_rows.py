"""The HostFactory AWS template examples in the docs load as AWS templates."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import FieldMappingRegistry
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.scheduler.hostfactory_field_mapping import AWSFieldMapping
from tests.providers.aws.template_defaults_support import make_defaults_service

DOC = Path(__file__).resolve().parents[4] / "docs/root/hostfactory/awsprov_templates.md"
EXAMPLES = re.findall(r"^``\n(\{.*?\n\})\n``$", DOC.read_text(), flags=re.S | re.M)


def test_examples_are_found():
    assert len(EXAMPLES) == 3


@pytest.mark.parametrize("raw", EXAMPLES, ids=lambda r: json.loads(r)["templateId"])
def test_documented_untyped_row_loads_as_aws_template(raw, tmp_path):
    row = json.loads(raw)
    assert "providerType" not in row and "provider_type" not in row

    strategy = HostFactorySchedulerStrategy(template_defaults_service=make_defaults_service())
    strategy._logger = MagicMock()
    path = tmp_path / "templates.json"
    path.write_text(json.dumps({"templates": [row]}))
    with (
        patch.object(
            FieldMappingRegistry,
            "get_or_none",
            side_effect=lambda t: AWSFieldMapping() if t == "aws" else None,
        ),
        patch.object(strategy, "_get_active_provider_type", return_value="aws"),
        patch.object(strategy, "_get_provider_name", return_value="aws-default"),
    ):
        loaded = strategy.load_templates_from_path(str(path))

    assert len(loaded) == 1
    factory = TemplateFactory()
    factory.register_provider_template_class("aws", AWSTemplate)
    template = factory.create_template(loaded[0])
    assert isinstance(template, AWSTemplate)
