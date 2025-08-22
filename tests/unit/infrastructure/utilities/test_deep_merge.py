"""Tests for deep merge utility."""

from infrastructure.utilities.common.deep_merge import deep_merge


class TestDeepMerge:
    """Test deep merge functionality."""

    def test_deep_merge_basic(self):
        """Test basic dictionary merging."""
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = deep_merge(base, override)

        assert result == {"a": 1, "b": 3, "c": 4}

    def test_deep_merge_nested_dicts(self):
        """Test nested dictionary merging."""
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 20, "z": 30}, "c": 4}
        result = deep_merge(base, override)

        expected = {"a": {"x": 1, "y": 20, "z": 30}, "b": 3, "c": 4}
        assert result == expected

    def test_deep_merge_array_override(self):
        """Test that arrays are replaced, not merged."""
        base = {"tags": [{"Key": "Env", "Value": "test"}]}
        override = {"tags": [{"Key": "Owner", "Value": "user"}]}
        result = deep_merge(base, override)

        assert result["tags"] == [{"Key": "Owner", "Value": "user"}]

    def test_deep_merge_aws_ec2fleet_scenario(self):
        """Test realistic AWS EC2Fleet merge scenario."""
        base = {
            "LaunchTemplateConfigs": [{"LaunchTemplateSpecification": {}}],
            "TargetCapacitySpecification": {
                "TotalTargetCapacity": 5,
                "DefaultTargetCapacityType": "on-demand",
            },
            "Type": "maintain",
        }

        override = {
            "TargetCapacitySpecification": {"TotalTargetCapacity": 10, "OnDemandTargetCapacity": 3},
            "SpotOptions": {"AllocationStrategy": "diversified"},
        }

        result = deep_merge(base, override)

        # User values should override
        assert result["TargetCapacitySpecification"]["TotalTargetCapacity"] == 10
        assert result["TargetCapacitySpecification"]["OnDemandTargetCapacity"] == 3

        # Default values should be preserved
        assert result["TargetCapacitySpecification"]["DefaultTargetCapacityType"] == "on-demand"
        assert result["Type"] == "maintain"
        assert result["LaunchTemplateConfigs"] == [{"LaunchTemplateSpecification": {}}]

        # New sections should be added
        assert result["SpotOptions"]["AllocationStrategy"] == "diversified"

    def test_deep_merge_empty_dicts(self):
        """Test merging with empty dictionaries."""
        assert deep_merge({}, {"a": 1}) == {"a": 1}
        assert deep_merge({"a": 1}, {}) == {"a": 1}
        assert deep_merge({}, {}) == {}

    def test_deep_merge_none_values(self):
        """Test merging with None values."""
        base = {"a": 1, "b": None}
        override = {"b": 2, "c": None}
        result = deep_merge(base, override)

        assert result == {"a": 1, "b": 2, "c": None}

    def test_deep_merge_mixed_types(self):
        """Test merging with mixed value types."""
        base = {"a": {"nested": True}, "b": [1, 2]}
        override = {"a": "string", "b": {"dict": True}}
        result = deep_merge(base, override)

        # Override should replace regardless of type
        assert result == {"a": "string", "b": {"dict": True}}
