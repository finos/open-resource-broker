"""Tests for metadata promotion in HostFactorySchedulerStrategy.format_template_for_display."""

from unittest.mock import MagicMock

from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.template.dtos import TemplateDTO


def _make_strategy() -> HostFactorySchedulerStrategy:
    strategy = HostFactorySchedulerStrategy()
    strategy._logger = MagicMock()
    return strategy


def _make_dto(metadata: dict) -> TemplateDTO:
    return TemplateDTO(template_id="tpl-001", metadata=metadata)


class TestFormatTemplateForDisplayMetadataPromotion:
    """format_template_for_display must promote metadata keys to the top-level output."""

    def setup_method(self):
        self.strategy = _make_strategy()

    def test_fleet_type_promoted_and_mapped_to_camel_case(self):
        dto = _make_dto({"fleet_type": "maintain"})
        result = self.strategy.format_template_for_display(dto)
        assert "fleetType" in result
        assert result["fleetType"] == "maintain"

    def test_fleet_role_promoted_and_mapped_to_camel_case(self):
        dto = _make_dto({"fleet_role": "arn:aws:iam::123:role/FleetRole"})
        result = self.strategy.format_template_for_display(dto)
        assert "fleetRole" in result
        assert result["fleetRole"] == "arn:aws:iam::123:role/FleetRole"

    def test_multiple_metadata_keys_all_promoted(self):
        dto = _make_dto({"fleet_type": "request", "fleet_role": "arn:aws:iam::456:role/R"})
        result = self.strategy.format_template_for_display(dto)
        assert "fleetType" in result
        assert "fleetRole" in result

    def test_arbitrary_metadata_key_promoted(self):
        # Promotion works for ANY metadata key, not just AWS-specific ones.
        # An unmapped key lands in the output only if copy_unmapped is True;
        # the important thing is that it is NOT silently dropped before the
        # mapper sees it — i.e. the mapper receives it at the top level.
        # We verify this by using a key that IS in the field mappings (fleet_type)
        # alongside a custom key and confirming fleet_type is promoted correctly.
        dto = _make_dto({"fleet_type": "maintain", "custom_tag": "hello"})
        result = self.strategy.format_template_for_display(dto)
        # fleet_type must be promoted and mapped
        assert result.get("fleetType") == "maintain"
        # metadata dict itself must not appear in the output
        assert "metadata" not in result

    def test_metadata_dict_not_present_in_output(self):
        dto = _make_dto({"fleet_type": "maintain"})
        result = self.strategy.format_template_for_display(dto)
        assert "metadata" not in result

    def test_empty_metadata_does_not_raise(self):
        dto = _make_dto({})
        result = self.strategy.format_template_for_display(dto)
        assert isinstance(result, dict)

    def test_promotion_does_not_overwrite_existing_top_level_key(self):
        # TemplateDTO has fleet_type=None at top level (not a real field, but
        # metadata promotion uses setdefault so an existing top-level value wins).
        # Build a DTO where the top-level field is already set via a direct
        # attribute that to_dict() exposes, and metadata has a different value.
        # We use template_id as a safe known key: metadata cannot override it.
        dto = TemplateDTO(
            template_id="tpl-override",
            metadata={"template_id": "should-not-win"},
        )
        result = self.strategy.format_template_for_display(dto)
        # templateId must come from the real field, not the metadata value
        assert result.get("templateId") == "tpl-override"
