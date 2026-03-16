"""Unit tests for storage.dynamodb_strategy deprecation warning."""

import json
import warnings


class TestStorageDeprecationWarning:
    def test_old_format_emits_deprecation_warning(self, tmp_path):
        """Loading a config with storage.dynamodb_strategy emits DeprecationWarning."""
        from orb.config.loader import ConfigurationLoader

        config = {
            "version": "2.0.0",
            "storage": {
                "strategy": "json",
                "dynamodb_strategy": {
                    "region": "us-east-1",
                    "profile": "default",
                    "table_prefix": "hostfactory",
                },
            },
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ConfigurationLoader.load(config_path=str(config_file))

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) >= 1
        assert "storage.dynamodb_strategy" in str(deprecation_warnings[0].message)
        assert "deprecated" in str(deprecation_warnings[0].message)

    def test_new_format_no_deprecation_warning(self, tmp_path):
        """Loading a config with new structure emits no DeprecationWarning."""
        from orb.config.loader import ConfigurationLoader

        config = {
            "version": "2.0.0",
            "storage": {"strategy": "json"},
            "provider": {
                "providers": [
                    {
                        "name": "aws-default",
                        "type": "aws",
                        "enabled": True,
                        "config": {
                            "region": "us-east-1",
                            "storage": {
                                "dynamodb": {
                                    "region": "us-east-1",
                                    "profile": "default",
                                    "table_prefix": "hostfactory",
                                }
                            },
                        },
                    }
                ]
            },
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ConfigurationLoader.load(config_path=str(config_file))

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 0

    def test_old_format_still_loads_correctly(self, tmp_path):
        """Old format config still loads without error — deprecation does not break functionality."""
        from orb.config.loader import ConfigurationLoader

        config = {
            "version": "2.0.0",
            "storage": {
                "strategy": "json",
                "dynamodb_strategy": {
                    "region": "eu-west-1",
                    "profile": "prod",
                    "table_prefix": "myapp",
                },
            },
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = ConfigurationLoader.load(config_path=str(config_file))

        assert result["storage"]["dynamodb_strategy"]["region"] == "eu-west-1"

    def test_warning_uses_deprecation_warning_category(self, tmp_path):
        """Warning must use DeprecationWarning category, not UserWarning or logger."""
        from orb.config.loader import ConfigurationLoader

        config = {
            "storage": {
                "strategy": "json",
                "dynamodb_strategy": {
                    "region": "us-east-1",
                    "profile": "default",
                    "table_prefix": "hf",
                },
            }
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ConfigurationLoader.load(config_path=str(config_file))

        dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        # Must not be a plain UserWarning
        assert not any(w.category is UserWarning for w in dep_warnings)
