"""Tests for ProviderTemplateStrategy."""

import json
import os
import shutil
import tempfile
from unittest.mock import Mock

import pytest

from src.config.managers.configuration_manager import ConfigurationManager
from src.config.schemas.provider_strategy_schema import (
    ProviderConfig,
    ProviderInstanceConfig,
)
from src.infrastructure.persistence.json.provider_template_strategy import (
    ProviderTemplateStrategy,
)


class TestProviderTemplateStrategy:
    """Test suite for ProviderTemplateStrategy."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test files."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def mock_config_manager(self):
        """Mock configuration manager with test provider config."""
        config_manager = Mock(spec=ConfigurationManager)

        provider_config = ProviderConfig(
            selection_policy="WEIGHTED_ROUND_ROBIN",
            providers=[
                ProviderInstanceConfig(
                    name="aws-us-east-1",
                    type="aws",
                    enabled=True,
                    priority=1,
                    weight=10,
                    capabilities=["EC2Fleet", "SpotFleet"],
                ),
                ProviderInstanceConfig(
                    name="aws-us-west-2",
                    type="aws",
                    enabled=True,
                    priority=2,
                    weight=5,
                    capabilities=["RunInstances"],
                ),
                ProviderInstanceConfig(
                    name="provider1-east-us",
                    type="provider1",
                    enabled=True,
                    priority=3,
                    weight=3,
                    capabilities=["VirtualMachines"],
                ),
            ],
        )

        config_manager.get_provider_config.return_value = provider_config
        return config_manager

    @pytest.fixture
    def sample_templates(self):
        """Sample template data for testing."""
        return {
            "main_templates": [
                {
                    "template_id": "main-template-1",
                    "image_id": "ami-main1",
                    "subnet_ids": ["subnet-main1"],
                    "max_instances": 2,
                },
                {
                    "template_id": "main-template-2",
                    "image_id": "ami-main2",
                    "subnet_ids": ["subnet-main2"],
                    "max_instances": 3,
                },
            ],
            "aws_provider_templates": [
                {
                    "template_id": "aws-template-1",
                    "provider_type": "aws",
                    "provider_api": "EC2Fleet",
                    "image_id": "ami-aws1",
                    "subnet_ids": ["subnet-aws1"],
                    "max_instances": 5,
                },
                {
                    "template_id": "main-template-1",  # Override main template
                    "provider_type": "aws",
                    "image_id": "ami-aws-override",
                    "subnet_ids": ["subnet-aws-override"],
                    "max_instances": 10,
                },
            ],
            "aws_instance_templates": [
                {
                    "template_id": "instance-template-1",
                    "provider_name": "aws-us-east-1",
                    "provider_api": "SpotFleet",
                    "image_id": "ami-instance1",
                    "subnet_ids": ["subnet-instance1"],
                    "max_instances": 1,
                },
                {
                    "template_id": "aws-template-1",  # Override provider template
                    "provider_name": "aws-us-east-1",
                    "provider_api": "EC2Fleet",
                    "image_id": "ami-instance-override",
                    "subnet_ids": ["subnet-instance-override"],
                    "max_instances": 20,
                },
            ],
            "legacy_templates": [
                {
                    "template_id": "legacy-template-1",
                    "image_id": "ami-legacy1",
                    "subnet_ids": ["subnet-legacy1"],
                    "max_instances": 1,
                }
            ],
        }

    def create_template_files(self, temp_dir: str, sample_templates: dict):
        """Create template files in temp directory."""
        # Main templates file
        main_file = os.path.join(temp_dir, "templates.json")
        with open(main_file, "w") as f:
            json.dump(sample_templates["main_templates"], f, indent=2)

        # AWS provider type templates
        aws_file = os.path.join(temp_dir, "awsprov_templates.json")
        with open(aws_file, "w") as f:
            json.dump(sample_templates["aws_provider_templates"], f, indent=2)

        # AWS instance-specific templates
        instance_file = os.path.join(temp_dir, "aws-us-east-1_templates.json")
        with open(instance_file, "w") as f:
            json.dump(sample_templates["aws_instance_templates"], f, indent=2)

        # Legacy templates
        legacy_file = os.path.join(temp_dir, "awsprov_templates.json")
        # Note: This will overwrite the provider type file, which is intentional for testing

        return main_file, aws_file, instance_file, legacy_file

    def test_discover_template_files(self, temp_dir, mock_config_manager, sample_templates):
        """Test template file discovery."""
        main_file, aws_file, instance_file, legacy_file = self.create_template_files(
            temp_dir, sample_templates
        )

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)

        # Should discover files in priority order
        assert len(strategy._template_files) >= 2
        assert instance_file in strategy._template_files
        assert main_file in strategy._template_files

    def test_load_merged_templates_priority(self, temp_dir, mock_config_manager, sample_templates):
        """Test template loading with priority override."""
        main_file, aws_file, instance_file, legacy_file = self.create_template_files(
            temp_dir, sample_templates
        )

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)
        templates = strategy._load_merged_templates()

        # Check that higher priority templates override lower priority ones
        assert "main-template-1" in templates
        assert "aws-template-1" in templates
        assert "instance-template-1" in templates

        # main-template-1 should be overridden by aws provider template
        main_template = templates["main-template-1"]
        assert main_template["image_id"] == "ami-aws-override"  # From AWS provider file

        # aws-template-1 should be overridden by instance template
        aws_template = templates["aws-template-1"]
        assert aws_template["image_id"] == "ami-instance-override"  # From instance file

    def test_find_by_id(self, temp_dir, mock_config_manager, sample_templates):
        """Test finding template by ID."""
        main_file, aws_file, instance_file, legacy_file = self.create_template_files(
            temp_dir, sample_templates
        )

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)

        # Find template that exists
        template = strategy.find_by_id("instance-template-1")
        assert template is not None
        assert template["template_id"] == "instance-template-1"
        assert template["provider_name"] == "aws-us-east-1"

        # Find template that doesn't exist
        template = strategy.find_by_id("non-existent")
        assert template is None

    def test_find_all(self, temp_dir, mock_config_manager, sample_templates):
        """Test finding all templates."""
        main_file, aws_file, instance_file, legacy_file = self.create_template_files(
            temp_dir, sample_templates
        )

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)
        templates = strategy.find_all()

        # Should return all unique templates
        template_ids = [t["template_id"] for t in templates]
        assert "main-template-1" in template_ids
        assert "main-template-2" in template_ids
        assert "aws-template-1" in template_ids
        assert "instance-template-1" in template_ids

        # Check that overrides are applied
        main_template = next(t for t in templates if t["template_id"] == "main-template-1")
        assert main_template["image_id"] == "ami-aws-override"

    def test_save_to_provider_instance_file(self, temp_dir, mock_config_manager):
        """Test saving template to provider instance-specific file."""
        main_file = os.path.join(temp_dir, "templates.json")

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)

        template_data = {
            "template_id": "new-instance-template",
            "provider_name": "aws-us-east-1",
            "provider_api": "EC2Fleet",
            "image_id": "ami-new",
            "subnet_ids": ["subnet-new"],
            "max_instances": 1,
        }

        strategy.save(template_data)

        # Check that file was created
        instance_file = os.path.join(temp_dir, "aws-us-east-1_templates.json")
        assert os.path.exists(instance_file)

        # Check file contents
        with open(instance_file, "r") as f:
            saved_data = json.load(f)

        assert len(saved_data) == 1
        assert saved_data[0]["template_id"] == "new-instance-template"

    def test_save_to_provider_type_file(self, temp_dir, mock_config_manager):
        """Test saving template to provider type-specific file."""
        main_file = os.path.join(temp_dir, "templates.json")

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)

        template_data = {
            "template_id": "new-provider-template",
            "provider_type": "aws",
            "provider_api": "SpotFleet",
            "image_id": "ami-new",
            "subnet_ids": ["subnet-new"],
            "max_instances": 2,
        }

        strategy.save(template_data)

        # Check that file was created
        provider_file = os.path.join(temp_dir, "awsprov_templates.json")
        assert os.path.exists(provider_file)

        # Check file contents
        with open(provider_file, "r") as f:
            saved_data = json.load(f)

        assert len(saved_data) == 1
        assert saved_data[0]["template_id"] == "new-provider-template"

    def test_save_to_main_file(self, temp_dir, mock_config_manager):
        """Test saving template to main file when no provider info."""
        main_file = os.path.join(temp_dir, "templates.json")

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)

        template_data = {
            "template_id": "new-main-template",
            "image_id": "ami-new",
            "subnet_ids": ["subnet-new"],
            "max_instances": 1,
        }

        strategy.save(template_data)

        # Check that main file was created
        assert os.path.exists(main_file)

        # Check file contents
        with open(main_file, "r") as f:
            saved_data = json.load(f)

        assert len(saved_data) == 1
        assert saved_data[0]["template_id"] == "new-main-template"

    def test_delete_template(self, temp_dir, mock_config_manager, sample_templates):
        """Test deleting template from files."""
        main_file, aws_file, instance_file, legacy_file = self.create_template_files(
            temp_dir, sample_templates
        )

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)

        # Delete template that exists in multiple files
        result = strategy.delete("main-template-1")
        assert result is True

        # Check that template was removed from files
        template = strategy.find_by_id("main-template-1")
        assert template is None

    def test_get_template_source_info(self, temp_dir, mock_config_manager, sample_templates):
        """Test getting template source information."""
        main_file, aws_file, instance_file, legacy_file = self.create_template_files(
            temp_dir, sample_templates
        )

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)

        # Get source info for instance template
        source_info = strategy.get_template_source_info("instance-template-1")
        assert source_info is not None
        assert source_info["file_type"] == "provider_instance"
        assert instance_file in source_info["source_file"]

        # Get source info for main template
        source_info = strategy.get_template_source_info("main-template-2")
        assert source_info is not None
        assert source_info["file_type"] == "main"

    def test_classify_file_type(self, temp_dir, mock_config_manager):
        """Test file type classification."""
        main_file = os.path.join(temp_dir, "templates.json")
        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)

        assert strategy._classify_file_type("templates.json") == "main"
        assert strategy._classify_file_type("awsprov_templates.json") == "legacy"
        assert strategy._classify_file_type("provider1prov_templates.json") == "provider_type"
        assert strategy._classify_file_type("aws-us-east-1_templates.json") == "provider_instance"
        assert strategy._classify_file_type("unknown_file.json") == "unknown"

    def test_cache_functionality(self, temp_dir, mock_config_manager, sample_templates):
        """Test template caching functionality."""
        main_file, aws_file, instance_file, legacy_file = self.create_template_files(
            temp_dir, sample_templates
        )

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)

        # First call should load from files
        templates1 = strategy.find_all()

        # Second call should use cache
        templates2 = strategy.find_all()

        assert len(templates1) == len(templates2)

        # Modify file and check cache refresh
        new_template = {
            "template_id": "cache-test",
            "image_id": "ami-cache",
            "subnet_ids": ["subnet-cache"],
            "max_instances": 1,
        }

        with open(main_file, "w") as f:
            json.dump([new_template], f, indent=2)

        # Cache should refresh automatically
        templates3 = strategy.find_all()
        template_ids = [t["template_id"] for t in templates3]
        assert "cache-test" in template_ids

    def test_refresh_cache(self, temp_dir, mock_config_manager):
        """Test manual cache refresh."""
        main_file = os.path.join(temp_dir, "templates.json")

        # Create initial file
        with open(main_file, "w") as f:
            json.dump(
                [
                    {
                        "template_id": "initial",
                        "image_id": "ami-1",
                        "subnet_ids": ["subnet-1"],
                        "max_instances": 1,
                    }
                ],
                f,
            )

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)

        templates1 = strategy.find_all()
        assert len(templates1) == 1

        # Add new file
        new_file = os.path.join(temp_dir, "aws-us-east-1_templates.json")
        with open(new_file, "w") as f:
            json.dump(
                [
                    {
                        "template_id": "new",
                        "image_id": "ami-2",
                        "subnet_ids": ["subnet-2"],
                        "max_instances": 1,
                    }
                ],
                f,
            )

        # Refresh cache
        strategy.refresh_cache()

        templates2 = strategy.find_all()
        template_ids = [t["template_id"] for t in templates2]
        assert "initial" in template_ids
        assert "new" in template_ids

    def test_error_handling_invalid_json(self, temp_dir, mock_config_manager):
        """Test error handling for invalid JSON files."""
        main_file = os.path.join(temp_dir, "templates.json")

        # Create invalid JSON file
        with open(main_file, "w") as f:
            f.write("invalid json content")

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)

        # Should handle error gracefully
        templates = strategy.find_all()
        assert templates == []

        template = strategy.find_by_id("any-id")
        assert template is None

    def test_error_handling_missing_template_id(self, temp_dir, mock_config_manager):
        """Test error handling for templates without template_id."""
        main_file = os.path.join(temp_dir, "templates.json")

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)

        # Try to save template without template_id
        with pytest.raises(ValueError, match="Template data must include 'template_id'"):
            strategy.save(
                {
                    "image_id": "ami-123",
                    "subnet_ids": ["subnet-123"],
                    "max_instances": 1,
                }
            )

    def test_object_format_templates(self, temp_dir, mock_config_manager):
        """Test loading templates in object format."""
        main_file = os.path.join(temp_dir, "templates.json")

        # Create templates in object format
        template_data = {
            "template1": {
                "image_id": "ami-1",
                "subnet_ids": ["subnet-1"],
                "max_instances": 1,
            },
            "template2": {
                "image_id": "ami-2",
                "subnet_ids": ["subnet-2"],
                "max_instances": 2,
            },
        }

        with open(main_file, "w") as f:
            json.dump(template_data, f, indent=2)

        strategy = ProviderTemplateStrategy(main_file, mock_config_manager)
        templates = strategy.find_all()

        assert len(templates) == 2
        template_ids = [t["template_id"] for t in templates]
        assert "template1" in template_ids
        assert "template2" in template_ids
