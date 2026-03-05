"""Contract tests enforcing which fields are present/absent per price type in HF template output.

These tests define the contract for getAvailableTemplates output. They are expected to fail
against the current implementation and should be made green by fixing the field filtering logic.
"""

import pytest

from application.dto.template_dto import TemplateDTO

# Fields that must never appear in ondemand templates
ONDEMAND_FORBIDDEN = {
    "maxSpotPrice",
    "vmTypesOnDemand",
    "vmTypesPriority",
    "percentOnDemand",
    "allocationStrategyOnDemand",
    "fleetRole",
}

# Fields that must never appear in spot templates
SPOT_FORBIDDEN = {
    "vmTypesOnDemand",
    "vmTypesPriority",
    "percentOnDemand",
    "allocationStrategyOnDemand",
}

# Fields that must be present in spot templates
SPOT_REQUIRED = {"maxSpotPrice"}

# Fields that must be present in heterogeneous templates
HETEROGENEOUS_REQUIRED = {"maxSpotPrice", "percentOnDemand"}


def _make_template(**kwargs) -> TemplateDTO:
    """Build a minimal TemplateDTO with sensible defaults, overridden by kwargs."""
    defaults = dict(
        template_id="tpl-test",
        name="Test Template",
        provider_api="EC2Fleet",
        image_id="ami-12345678",
        max_instances=10,
        machine_types={"m5.large": 1},
        subnet_ids=["subnet-aaa"],
        security_group_ids=["sg-bbb"],
    )
    defaults.update(kwargs)
    return TemplateDTO(**defaults)


class TestOndemandTemplateFields:
    """Ondemand templates must not carry spot/mixed-fleet fields."""

    def test_ondemand_does_not_emit_max_spot_price(self, hf_strategy):
        template = _make_template(price_type="ondemand")
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "maxSpotPrice" not in output, "ondemand template must not contain maxSpotPrice"

    def test_ondemand_does_not_emit_vm_types_ondemand(self, hf_strategy):
        template = _make_template(price_type="ondemand")
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "vmTypesOnDemand" not in output, "ondemand template must not contain vmTypesOnDemand"

    def test_ondemand_does_not_emit_vm_types_priority(self, hf_strategy):
        template = _make_template(price_type="ondemand")
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "vmTypesPriority" not in output, "ondemand template must not contain vmTypesPriority"

    def test_ondemand_does_not_emit_percent_on_demand(self, hf_strategy):
        template = _make_template(price_type="ondemand")
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "percentOnDemand" not in output, "ondemand template must not contain percentOnDemand"

    def test_ondemand_does_not_emit_allocation_strategy_ondemand(self, hf_strategy):
        template = _make_template(price_type="ondemand")
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "allocationStrategyOnDemand" not in output, (
            "ondemand template must not contain allocationStrategyOnDemand"
        )

    def test_ondemand_does_not_emit_fleet_role(self, hf_strategy):
        template = _make_template(price_type="ondemand")
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "fleetRole" not in output, "ondemand template must not contain fleetRole"

    def test_ondemand_forbidden_fields_absent_as_a_set(self, hf_strategy):
        """All forbidden ondemand fields are absent in a single assertion."""
        template = _make_template(price_type="ondemand")
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        present_forbidden = ONDEMAND_FORBIDDEN & output.keys()
        assert not present_forbidden, (
            f"ondemand template contains forbidden fields: {present_forbidden}"
        )


class TestSpotTemplateFields:
    """Spot templates must carry maxSpotPrice and must not carry mixed-fleet fields."""

    def test_spot_emits_max_spot_price(self, hf_strategy):
        template = _make_template(price_type="spot", max_price=0.05)
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "maxSpotPrice" in output, "spot template must contain maxSpotPrice"

    def test_spot_max_spot_price_value_is_correct(self, hf_strategy):
        template = _make_template(price_type="spot", max_price=0.05)
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert output.get("maxSpotPrice") == 0.05

    def test_spot_does_not_emit_vm_types_ondemand(self, hf_strategy):
        template = _make_template(price_type="spot", max_price=0.05)
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "vmTypesOnDemand" not in output, "spot template must not contain vmTypesOnDemand"

    def test_spot_does_not_emit_vm_types_priority(self, hf_strategy):
        template = _make_template(price_type="spot", max_price=0.05)
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "vmTypesPriority" not in output, "spot template must not contain vmTypesPriority"

    def test_spot_does_not_emit_percent_on_demand(self, hf_strategy):
        template = _make_template(price_type="spot", max_price=0.05)
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "percentOnDemand" not in output, "spot template must not contain percentOnDemand"

    def test_spot_does_not_emit_allocation_strategy_ondemand(self, hf_strategy):
        template = _make_template(price_type="spot", max_price=0.05)
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "allocationStrategyOnDemand" not in output, (
            "spot template must not contain allocationStrategyOnDemand"
        )

    def test_spot_forbidden_fields_absent_as_a_set(self, hf_strategy):
        """All forbidden spot fields are absent in a single assertion."""
        template = _make_template(price_type="spot", max_price=0.05)
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        present_forbidden = SPOT_FORBIDDEN & output.keys()
        assert not present_forbidden, (
            f"spot template contains forbidden fields: {present_forbidden}"
        )


class TestHeterogeneousTemplateFields:
    """Heterogeneous (mixed) templates must carry maxSpotPrice and percentOnDemand."""

    def test_heterogeneous_emits_max_spot_price(self, hf_strategy):
        template = _make_template(
            price_type="heterogeneous",
            max_price=0.10,
            percent_on_demand=50,
        )
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "maxSpotPrice" in output, "heterogeneous template must contain maxSpotPrice"

    def test_heterogeneous_emits_percent_on_demand(self, hf_strategy):
        template = _make_template(
            price_type="heterogeneous",
            max_price=0.10,
            percent_on_demand=50,
        )
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "percentOnDemand" in output, "heterogeneous template must contain percentOnDemand"

    def test_heterogeneous_percent_on_demand_value_is_correct(self, hf_strategy):
        template = _make_template(
            price_type="heterogeneous",
            max_price=0.10,
            percent_on_demand=50,
        )
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert output.get("percentOnDemand") == 50

    def test_heterogeneous_required_fields_present_as_a_set(self, hf_strategy):
        """All required heterogeneous fields are present in a single assertion."""
        template = _make_template(
            price_type="heterogeneous",
            max_price=0.10,
            percent_on_demand=50,
        )
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        missing_required = HETEROGENEOUS_REQUIRED - output.keys()
        assert not missing_required, (
            f"heterogeneous template is missing required fields: {missing_required}"
        )

    def test_heterogeneous_optional_vm_types_ondemand_only_when_non_empty(self, hf_strategy):
        """vmTypesOnDemand must not appear when the underlying value is empty."""
        template = _make_template(
            price_type="heterogeneous",
            max_price=0.10,
            percent_on_demand=50,
        )
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "vmTypesOnDemand" not in output, "vmTypesOnDemand must not be emitted when empty"

    def test_heterogeneous_optional_vm_types_priority_only_when_non_empty(self, hf_strategy):
        """vmTypesPriority must not appear when the underlying value is empty."""
        template = _make_template(
            price_type="heterogeneous",
            max_price=0.10,
            percent_on_demand=50,
        )
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "vmTypesPriority" not in output, "vmTypesPriority must not be emitted when empty"

    def test_heterogeneous_optional_allocation_strategy_ondemand_only_when_non_empty(
        self, hf_strategy
    ):
        """allocationStrategyOnDemand must not appear when the underlying value is empty."""
        template = _make_template(
            price_type="heterogeneous",
            max_price=0.10,
            percent_on_demand=50,
        )
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        assert "allocationStrategyOnDemand" not in output, (
            "allocationStrategyOnDemand must not be emitted when empty"
        )


class TestEmptyFieldSuppression:
    """Empty dicts and empty lists must not be emitted for any price type."""

    @pytest.mark.parametrize("price_type", ["ondemand", "spot", "heterogeneous"])
    def test_empty_dict_fields_not_emitted(self, hf_strategy, price_type):
        kwargs = dict(price_type=price_type)
        if price_type in ("spot", "heterogeneous"):
            kwargs["max_price"] = 0.05
        if price_type == "heterogeneous":
            kwargs["percent_on_demand"] = 50
        template = _make_template(**kwargs)
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        empty_dicts = [k for k, v in output.items() if v == {}]
        assert not empty_dicts, f"{price_type} template emits empty dicts for fields: {empty_dicts}"

    @pytest.mark.parametrize("price_type", ["ondemand", "spot", "heterogeneous"])
    def test_empty_list_fields_not_emitted(self, hf_strategy, price_type):
        kwargs = dict(price_type=price_type, subnet_ids=[], security_group_ids=[])
        if price_type in ("spot", "heterogeneous"):
            kwargs["max_price"] = 0.05
        if price_type == "heterogeneous":
            kwargs["percent_on_demand"] = 50
        template = _make_template(**kwargs)
        result = hf_strategy.format_templates_response([template])
        output = result["templates"][0]
        empty_lists = [k for k, v in output.items() if v == []]
        assert not empty_lists, f"{price_type} template emits empty lists for fields: {empty_lists}"
