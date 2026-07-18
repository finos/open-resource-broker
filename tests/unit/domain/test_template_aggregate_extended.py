"""Extended unit tests for Template aggregate covering uncovered branches."""

import pytest

from orb.domain.template.template_aggregate import Template

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tpl(**kwargs):
    defaults = {"template_id": "tpl-test", "max_instances": 2}
    defaults.update(kwargs)
    return Template(**defaults)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateValidation:
    def test_raises_when_max_instances_zero(self):
        with pytest.raises(Exception):
            _tpl(max_instances=0)

    def test_raises_when_max_instances_negative(self):
        with pytest.raises(Exception):
            _tpl(max_instances=-1)

    def test_raises_on_reserved_orb_tag_key(self):
        with pytest.raises(Exception, match="orb:"):
            _tpl(tags={"orb:internal": "yes"})

    def test_multiple_reserved_tag_keys_all_reported(self):
        with pytest.raises(Exception) as exc_info:
            _tpl(tags={"orb:a": "1", "orb:b": "2"})
        msg = str(exc_info.value)
        assert "orb:a" in msg or "orb:b" in msg

    def test_non_reserved_tag_is_accepted(self):
        tpl = _tpl(tags={"Env": "prod"})
        assert tpl.tags["Env"] == "prod"


# ---------------------------------------------------------------------------
# Provider field validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateProviderFields:
    def test_provider_type_extracted_from_provider_name_with_dash(self):
        tpl = _tpl(provider_name="aws-us-east-1")
        assert tpl.provider_type == "aws"

    def test_provider_name_without_dash_sets_whole_name_as_type(self):
        tpl = _tpl(provider_name="aws")
        assert tpl.provider_type == "aws"

    def test_explicit_provider_type_not_overwritten_by_name(self):
        tpl = _tpl(provider_name="aws-us-east-1", provider_type="aws")
        assert tpl.provider_type == "aws"

    def test_invalid_provider_name_format_raises(self):
        with pytest.raises(Exception, match="provider_name"):
            _tpl(provider_name="aws us east 1")

    def test_uppercase_provider_type_raises(self):
        with pytest.raises(Exception, match="provider_type"):
            _tpl(provider_type="AWS")


# ---------------------------------------------------------------------------
# Allocation strategy defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateAllocationStrategy:
    def test_spot_price_type_sets_price_capacity_optimized(self):
        tpl = _tpl(price_type="spot")
        assert tpl.allocation_strategy == "priceCapacityOptimized"

    def test_ondemand_price_type_sets_lowest_price(self):
        tpl = _tpl(price_type="ondemand")
        assert tpl.allocation_strategy == "lowestPrice"

    def test_heterogeneous_price_type_sets_lowest_price(self):
        tpl = _tpl(price_type="heterogeneous")
        assert tpl.allocation_strategy == "lowestPrice"


# ---------------------------------------------------------------------------
# Subnet/security group mutations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateSubnetAndSG:
    def test_add_subnet(self):
        tpl = _tpl()
        updated = tpl.add_subnet("subnet-001")
        assert "subnet-001" in updated.subnet_ids

    def test_add_subnet_idempotent(self):
        tpl = _tpl()
        tpl = tpl.add_subnet("subnet-001")
        tpl = tpl.add_subnet("subnet-001")
        assert tpl.subnet_ids.count("subnet-001") == 1

    def test_remove_subnet(self):
        tpl = _tpl()
        tpl = tpl.add_subnet("subnet-001")
        tpl = tpl.remove_subnet("subnet-001")
        assert "subnet-001" not in tpl.subnet_ids

    def test_remove_nonexistent_subnet_is_noop(self):
        tpl = _tpl()
        updated = tpl.remove_subnet("nonexistent")
        assert updated is tpl

    def test_subnet_id_property_returns_first(self):
        tpl = _tpl(subnet_ids=["subnet-001", "subnet-002"])
        assert tpl.subnet_id == "subnet-001"

    def test_subnet_id_property_returns_none_when_empty(self):
        tpl = _tpl()
        assert tpl.subnet_id is None

    def test_add_security_group(self):
        tpl = _tpl()
        updated = tpl.add_security_group("sg-001")
        assert "sg-001" in updated.security_group_ids

    def test_add_security_group_idempotent(self):
        tpl = _tpl()
        tpl = tpl.add_security_group("sg-001")
        tpl = tpl.add_security_group("sg-001")
        assert tpl.security_group_ids.count("sg-001") == 1

    def test_remove_security_group(self):
        tpl = _tpl()
        tpl = tpl.add_security_group("sg-001")
        tpl = tpl.remove_security_group("sg-001")
        assert "sg-001" not in tpl.security_group_ids

    def test_remove_nonexistent_sg_is_noop(self):
        tpl = _tpl()
        updated = tpl.remove_security_group("nonexistent")
        assert updated is tpl


# ---------------------------------------------------------------------------
# Image update
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateImageUpdate:
    def test_update_image_id(self):
        tpl = _tpl()
        updated = tpl.update_image_id("ami-new")
        assert updated.image_id == "ami-new"

    def test_update_image_id_does_not_mutate_original(self):
        tpl = _tpl(image_id="ami-old")
        tpl.update_image_id("ami-new")
        assert tpl.image_id == "ami-old"


# ---------------------------------------------------------------------------
# Deprecated field names
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateDeprecatedFields:
    def test_instance_type_alias_accepted_with_deprecation_warning(self):
        with pytest.warns(DeprecationWarning, match="instance_type"):
            tpl = Template(template_id="tpl-d", instance_type="m5.large", max_instances=1)
        assert tpl.machine_type == "m5.large"

    def test_instance_profile_alias_accepted_with_deprecation_warning(self):
        with pytest.warns(DeprecationWarning, match="instance_profile"):
            tpl = Template(template_id="tpl-d2", instance_profile="my-role", max_instances=1)
        assert tpl.machine_role == "my-role"


# ---------------------------------------------------------------------------
# __str__ / __repr__
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateStringRepresentation:
    def test_str_contains_template_id(self):
        tpl = _tpl()
        assert "tpl-test" in str(tpl)

    def test_repr_contains_template_id(self):
        tpl = _tpl()
        assert "tpl-test" in repr(tpl)
