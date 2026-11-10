"""The persisted/wire template shape is pinned by its field set.

``TemplateDTO`` is what REST responses, the SQL layer and the schedulers
share. A field added, renamed or removed changes that shape for every
consumer, so such a change must edit this list deliberately.
"""

from __future__ import annotations

import pytest

from orb.api.models.responses import TemplateItem
from orb.application.dto.template import TemplateDTO

_TEMPLATE_DTO_FIELDS = [
    "allocation_strategy",
    "allocation_strategy_on_demand",
    "created_at",
    "description",
    "encryption_key",
    "fallback_to_ondemand",
    "image_id",
    "instance_profile",
    "iops",
    "is_active",
    "key_name",
    "machine_types",
    "machine_types_ondemand",
    "machine_types_priority",
    "max_instances",
    "max_price",
    "metadata",
    "monitoring_enabled",
    "name",
    "network_zones",
    "price_type",
    "provider_api",
    "provider_config",
    "provider_data",
    "provider_name",
    "provider_type",
    "public_ip_assignment",
    "root_device_volume_size",
    "security_group_ids",
    "storage_encryption",
    "subnet_ids",
    "tags",
    "template_id",
    "throughput",
    "updated_at",
    "user_data",
    "version",
    "volume_type",
]


@pytest.mark.unit
def test_template_dto_field_set_is_pinned() -> None:
    assert sorted(TemplateDTO.model_fields) == _TEMPLATE_DTO_FIELDS


@pytest.mark.unit
def test_rest_template_item_exposes_the_neutral_network_and_aws_origin_fields() -> None:
    fields = set(TemplateItem.model_fields)
    assert {"subnet_ids", "security_group_ids", "iops", "throughput"} <= fields
    assert {"machine_types_ondemand", "machine_types_priority"} <= fields
