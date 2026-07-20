"""Unit tests for operator-facing config-key deprecation warnings.

These config-key deprecations are detected while loading a config dict at
startup, so per the house deprecation standard (Pattern 2 in
``docs/root/developer_guide/deprecation.md``) they are emitted via
``logger.warning`` — not ``warnings.warn(DeprecationWarning)``, which is
filtered by ``filterwarnings`` in production and CI and never reaches
operators.  Tests therefore assert against ``caplog``.
"""

import json
import logging


class TestStorageDeprecationWarning:
    def test_old_format_emits_deprecation_log_warning(self, tmp_path, caplog):
        """Loading a config with storage.dynamodb_strategy logs a deprecation warning."""
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

        with caplog.at_level(logging.WARNING, logger="orb.config.loader"):
            ConfigurationLoader.load(config_path=str(config_file))

        matching = [
            r
            for r in caplog.records
            if "storage.dynamodb_strategy" in r.getMessage() and "deprecated" in r.getMessage()
        ]
        assert len(matching) >= 1
        assert matching[0].levelno == logging.WARNING

    def test_new_format_no_deprecation_log_warning(self, tmp_path, caplog):
        """Loading a config with new structure logs no deprecation warning."""
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

        with caplog.at_level(logging.WARNING, logger="orb.config.loader"):
            ConfigurationLoader.load(config_path=str(config_file))

        matching = [r for r in caplog.records if "storage.dynamodb_strategy" in r.getMessage()]
        assert len(matching) == 0

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

        result = ConfigurationLoader.load(config_path=str(config_file))

        assert result["storage"]["dynamodb_strategy"]["region"] == "eu-west-1"

    def test_warning_message_names_replacement_and_removal_horizon(self, tmp_path, caplog):
        """The deprecation message must name the replacement and the removal version."""
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

        with caplog.at_level(logging.WARNING, logger="orb.config.loader"):
            ConfigurationLoader.load(config_path=str(config_file))

        matching = [r for r in caplog.records if "storage.dynamodb_strategy" in r.getMessage()]
        assert len(matching) >= 1
        message = matching[0].getMessage()
        assert "ORB 3.0" in message
        assert "provider.providers" in message


class TestBatchSizesDeprecationWarning:
    def test_old_format_emits_deprecation_log_warning(self, tmp_path, caplog):
        """Loading a config with performance.batch_sizes logs a deprecation warning."""
        from orb.config.loader import ConfigurationLoader

        config = {
            "version": "2.0.0",
            "storage": {"strategy": "json"},
            "performance": {
                "batch_sizes": {
                    "provisioning": 10,
                    "termination": 5,
                },
            },
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with caplog.at_level(logging.WARNING, logger="orb.config.loader"):
            ConfigurationLoader.load(config_path=str(config_file))

        matching = [
            r
            for r in caplog.records
            if "performance.batch_sizes" in r.getMessage() and "deprecated" in r.getMessage()
        ]
        assert len(matching) >= 1
        assert matching[0].levelno == logging.WARNING
        assert "ORB 3.0" in matching[0].getMessage()

    def test_new_format_no_deprecation_log_warning(self, tmp_path, caplog):
        """A config without performance.batch_sizes logs no batch_sizes deprecation warning."""
        from orb.config.loader import ConfigurationLoader

        config = {
            "version": "2.0.0",
            "storage": {"strategy": "json"},
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with caplog.at_level(logging.WARNING, logger="orb.config.loader"):
            ConfigurationLoader.load(config_path=str(config_file))

        matching = [r for r in caplog.records if "performance.batch_sizes" in r.getMessage()]
        assert len(matching) == 0
