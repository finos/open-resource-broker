"""Tests verifying all known HF-spec fields have entries in HostFactoryFieldMappings."""

from orb.infrastructure.scheduler.hostfactory.field_mappings import HostFactoryFieldMappings

# Fields that every HF provider must be able to map (generic + AWS-specific combined)
REQUIRED_GENERIC_FIELDS = [
    "templateId",
    "maxNumber",
    "imageId",
    "keyName",
    "fleetType",
    "vmType",
    "vmTypes",
    "priceType",
    "maxSpotPrice",
    "allocationStrategy",
    "rootDeviceVolumeSize",
    "volumeType",
    "iops",
    "instanceTags",
    "name",
    "providerName",
    "providerApi",
    "providerType",
    "createdAt",
]

REQUIRED_AWS_FIELDS = [
    # AWS VPC network configuration
    "subnetId",
    "subnetIds",
    "securityGroupIds",
    # AWS fleet/instance configuration
    "vmTypesOnDemand",
    "vmTypesPriority",
    "abisInstanceRequirements",
    "percentOnDemand",
    "allocationStrategyOnDemand",
    "fleetRole",
    "spotFleetRequestExpiry",
    "poolsCount",
    "launchTemplateId",
    "instanceProfile",
    "userDataScript",
]


class TestFieldMappingCoverage:
    """Verify HostFactoryFieldMappings covers all known HF-spec fields."""

    def test_generic_fields_present_in_generic_mappings(self):
        """Every required generic field must exist in the generic mapping table."""
        generic = HostFactoryFieldMappings.MAPPINGS.get("generic", {})
        missing = [f for f in REQUIRED_GENERIC_FIELDS if f not in generic]
        assert not missing, f"Missing generic field mappings: {missing}"

    def test_aws_fields_present_in_aws_mappings(self):
        """Every required AWS-specific field must exist in the aws mapping table."""
        aws = HostFactoryFieldMappings.MAPPINGS.get("aws", {})
        missing = [f for f in REQUIRED_AWS_FIELDS if f not in aws]
        assert not missing, f"Missing AWS field mappings: {missing}"

    def test_get_mappings_aws_includes_all_fields(self):
        """get_mappings('aws') must return the union of generic and AWS fields."""
        combined = HostFactoryFieldMappings.get_mappings("aws")
        all_required = REQUIRED_GENERIC_FIELDS + REQUIRED_AWS_FIELDS
        missing = [f for f in all_required if f not in combined]
        assert not missing, f"get_mappings('aws') is missing fields: {missing}"

    def test_generic_fields_map_to_non_empty_internal_names(self):
        """Every generic mapping must resolve to a non-empty internal field name."""
        generic = HostFactoryFieldMappings.MAPPINGS.get("generic", {})
        empty = [hf for hf, internal in generic.items() if not internal]
        assert not empty, f"Generic fields map to empty internal names: {empty}"

    def test_aws_fields_map_to_non_empty_internal_names(self):
        """Every AWS mapping must resolve to a non-empty internal field name."""
        aws = HostFactoryFieldMappings.MAPPINGS.get("aws", {})
        empty = [hf for hf, internal in aws.items() if not internal]
        assert not empty, f"AWS fields map to empty internal names: {empty}"

    def test_aws_is_a_supported_provider(self):
        """'aws' must appear in the list of supported providers."""
        assert "aws" in HostFactoryFieldMappings.get_supported_providers()

    def test_no_overlap_between_generic_and_aws_keys(self):
        """Generic and AWS mapping tables must not share the same HF field name."""
        generic_keys = set(HostFactoryFieldMappings.MAPPINGS.get("generic", {}).keys())
        aws_keys = set(HostFactoryFieldMappings.MAPPINGS.get("aws", {}).keys())
        overlap = generic_keys & aws_keys
        assert not overlap, f"Field names appear in both generic and AWS tables: {overlap}"
