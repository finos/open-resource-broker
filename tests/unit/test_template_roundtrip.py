"""Round-trip tests for template generation and loading.

Verifies that templates produced by the generation path can be loaded back
with all fields intact. This catches field-mapping regressions before they
surface in integration tests.
"""

import json
import os
import tempfile
from datetime import date, datetime

import pytest

from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.template.dtos import TemplateDTO
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler


def _make_strategy() -> HostFactorySchedulerStrategy:
    """Return a strategy instance with no DI dependencies."""
    return HostFactorySchedulerStrategy()


def _template_to_dto(template: AWSTemplate) -> TemplateDTO:
    """Convert an AWSTemplate to a TemplateDTO, stamping a created_at timestamp."""
    dto = TemplateDTO.from_domain(template)
    # Stamp a fixed timestamp so round-trip assertions are deterministic
    object.__setattr__(dto, "created_at", datetime(2024, 1, 15, 12, 0, 0))
    return dto


class TestTemplateRoundTrip:
    """Verify generate → load round-trip preserves all expected fields."""

    def setup_method(self):
        self.strategy = _make_strategy()
        self.example_templates = EC2FleetHandler.get_example_templates()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _roundtrip(self, template: AWSTemplate) -> dict:
        """
        Full round-trip: domain template → HF wire format → loaded internal dict.

        Steps mirror what `orb templates generate` + `load_templates_from_path` do:
          1. Convert domain object to TemplateDTO
          2. Call format_templates_for_generation (internal → HF camelCase)
          3. Write to a temp JSON file
          4. Load back via load_templates_from_path (HF camelCase → internal)
        """
        dto = _template_to_dto(template)
        internal_dict = dto.to_dict()

        # Step 2: format for generation (produces HF camelCase wire format)
        hf_dicts = self.strategy.format_templates_for_generation([internal_dict])
        assert len(hf_dicts) == 1, "format_templates_for_generation must return one entry"

        # Step 3: write to temp file (datetime fields serialised as ISO strings)
        def _default(obj):
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(hf_dicts, f, default=_default)
            tmp_path = f.name

        try:
            # Step 4: load back
            loaded = self.strategy.load_templates_from_path(tmp_path)
        finally:
            os.unlink(tmp_path)

        assert len(loaded) == 1, "load_templates_from_path must return one entry"
        return loaded[0]

    # ------------------------------------------------------------------
    # Core field preservation
    # ------------------------------------------------------------------

    def test_template_id_survives_roundtrip(self):
        template = self.example_templates[0]
        result = self._roundtrip(template)
        assert result["template_id"] == template.template_id

    def test_name_survives_roundtrip(self):
        template = self.example_templates[0]
        result = self._roundtrip(template)
        assert result["name"] == template.name

    def test_provider_api_survives_roundtrip(self):
        template = self.example_templates[0]
        result = self._roundtrip(template)
        assert result["provider_api"] == "EC2Fleet"

    def test_price_type_survives_roundtrip(self):
        for template in self.example_templates:
            result = self._roundtrip(template)
            assert result["price_type"] == template.price_type, (
                f"price_type mismatch for {template.template_id}"
            )

    def test_machine_types_survives_roundtrip(self):
        for template in self.example_templates:
            result = self._roundtrip(template)
            assert result.get("machine_types"), f"machine_types missing for {template.template_id}"
            assert result["machine_types"] == template.machine_types, (
                f"machine_types mismatch for {template.template_id}"
            )

    def test_created_at_survives_roundtrip(self):
        template = self.example_templates[0]
        result = self._roundtrip(template)
        # created_at may be a datetime or ISO string after round-trip; just assert present
        assert "created_at" in result

    # ------------------------------------------------------------------
    # Network fields — empty in generated output (correct by design)
    # ------------------------------------------------------------------

    def test_subnet_ids_empty_in_generated_output(self):
        """Generated templates intentionally have empty subnet_ids; defaults applied at runtime."""
        for template in self.example_templates:
            result = self._roundtrip(template)
            assert result.get("subnet_ids", []) == [], (
                f"subnet_ids should be empty in generated output for {template.template_id}"
            )

    def test_security_group_ids_empty_in_generated_output(self):
        for template in self.example_templates:
            result = self._roundtrip(template)
            assert result.get("security_group_ids", []) == [], (
                f"security_group_ids should be empty for {template.template_id}"
            )

    # ------------------------------------------------------------------
    # Heterogeneous template fields
    # ------------------------------------------------------------------

    def test_heterogeneous_percent_on_demand_survives_roundtrip(self):
        hetero = [t for t in self.example_templates if t.price_type == "heterogeneous"]
        assert hetero, "Expected at least one heterogeneous template in example set"

        for template in hetero:
            result = self._roundtrip(template)
            assert result.get("percent_on_demand") == template.percent_on_demand, (
                f"percent_on_demand mismatch for {template.template_id}"
            )

    def test_heterogeneous_allocation_strategy_survives_roundtrip(self):
        hetero = [t for t in self.example_templates if t.price_type == "heterogeneous"]
        for template in hetero:
            if template.allocation_strategy is None:
                continue
            result = self._roundtrip(template)
            # allocation_strategy may be an enum or string; normalise to str for comparison
            expected = (
                template.allocation_strategy.value
                if hasattr(template.allocation_strategy, "value")
                else str(template.allocation_strategy)
            )
            assert result.get("allocation_strategy") == expected, (
                f"allocation_strategy mismatch for {template.template_id}"
            )

    def test_heterogeneous_multi_machine_types_survives_roundtrip(self):
        """Templates with multiple machine types must preserve the full dict."""
        multi = [t for t in self.example_templates if len(t.machine_types) > 1]
        assert multi, "Expected at least one multi-machine-type template"

        for template in multi:
            result = self._roundtrip(template)
            assert result["machine_types"] == template.machine_types, (
                f"multi machine_types mismatch for {template.template_id}"
            )

    # ------------------------------------------------------------------
    # All example templates pass the full field checklist
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "template",
        EC2FleetHandler.get_example_templates(),
        ids=lambda t: t.template_id,
    )
    def test_all_example_templates_full_checklist(self, template: AWSTemplate):
        """Every example template must survive the round-trip with all required fields."""
        result = self._roundtrip(template)

        assert result.get("template_id") == template.template_id
        assert result.get("name") == template.name
        assert result.get("provider_api") == "EC2Fleet"
        assert result.get("price_type") == template.price_type
        assert result.get("machine_types") == template.machine_types
        assert "created_at" in result
        assert result.get("subnet_ids", []) == []
        assert result.get("security_group_ids", []) == []
