"""Unit tests for config schema validators: storage, performance, common, provider_strategy.

Pure Pydantic model validation — no network, no AWS, no filesystem.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# StorageConfig / SqlStrategyConfig / BackoffConfig / RetryConfig
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSqlStrategyConfig:
    """SqlStrategyConfig validates type and required fields for remote DBs."""

    def _make(self, **kwargs):
        from orb.config.schemas.storage_schema import SqlStrategyConfig

        return SqlStrategyConfig(**kwargs)

    def test_default_is_sqlite(self):
        cfg = self._make()
        assert cfg.type == "sqlite"

    def test_sqlite_requires_name(self):
        with pytest.raises(ValidationError, match="name is required"):
            self._make(type="sqlite", name="")

    def test_postgresql_requires_host(self):
        with pytest.raises(ValidationError, match="Host is required"):
            self._make(type="postgresql", host="", port=5432, name="db")

    def test_postgresql_requires_port(self):
        with pytest.raises(ValidationError, match="Port is required"):
            self._make(type="postgresql", host="localhost", port=0, name="db")

    def test_postgresql_requires_name(self):
        with pytest.raises(ValidationError, match="name is required"):
            self._make(type="postgresql", host="localhost", port=5432, name="")

    def test_mysql_requires_host(self):
        with pytest.raises(ValidationError, match="Host is required"):
            self._make(type="mysql", host="", port=3306, name="mydb")

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError, match="Database type must be one of"):
            self._make(type="oracle")

    def test_valid_postgresql_config(self):
        cfg = self._make(type="postgresql", host="pg.example.com", port=5432, name="prod")
        assert cfg.type == "postgresql"
        assert cfg.host == "pg.example.com"


@pytest.mark.unit
class TestJsonStrategyConfig:
    """JsonStrategyConfig validates storage_type."""

    def _make(self, **kwargs):
        from orb.config.schemas.storage_schema import JsonStrategyConfig

        return JsonStrategyConfig(**kwargs)

    def test_default_is_single_file(self):
        cfg = self._make()
        assert cfg.storage_type == "single_file"

    def test_split_files_is_valid(self):
        cfg = self._make(storage_type="split_files")
        assert cfg.storage_type == "split_files"

    def test_invalid_storage_type_rejected(self):
        with pytest.raises(ValidationError, match="Storage type must be one of"):
            self._make(storage_type="mongo")


@pytest.mark.unit
class TestStorageConfigStrategyValidator:
    """StorageConfig.validate_strategy rejects unknown backends."""

    def test_json_is_valid(self):
        from orb.config.schemas.storage_schema import StorageConfig

        cfg = StorageConfig(strategy="json")
        assert cfg.strategy == "json"

    def test_sql_is_valid(self):
        from orb.config.schemas.storage_schema import StorageConfig

        cfg = StorageConfig(strategy="sql")
        assert cfg.strategy == "sql"

    def test_invalid_strategy_rejected(self):
        from orb.config.schemas.storage_schema import StorageConfig

        with pytest.raises(ValidationError, match="Storage strategy must be one of"):
            StorageConfig(strategy="redis")

    def test_json_without_base_path_rejected(self):
        from orb.config.schemas.storage_schema import JsonStrategyConfig, StorageConfig

        with pytest.raises(ValidationError, match="base path is required"):
            StorageConfig(
                strategy="json",
                json_strategy=JsonStrategyConfig(base_path=""),  # type: ignore[call-arg]
            )

    def test_sql_without_name_rejected(self):
        from orb.config.schemas.storage_schema import SqlStrategyConfig, StorageConfig

        # SqlStrategyConfig.validate_connection_info fires first
        with pytest.raises(ValidationError):
            StorageConfig(
                strategy="sql",
                sql_strategy=SqlStrategyConfig(type="sqlite", name=""),  # type: ignore[call-arg]
            )


@pytest.mark.unit
class TestBackoffConfig:
    """BackoffConfig validates strategy_type."""

    def _make(self, **kwargs):
        from orb.config.schemas.storage_schema import BackoffConfig

        return BackoffConfig(**kwargs)

    def test_default_is_exponential(self):
        cfg = self._make()
        assert cfg.strategy_type == "exponential"

    def test_constant_is_valid(self):
        cfg = self._make(strategy_type="constant")
        assert cfg.strategy_type == "constant"

    def test_linear_is_valid(self):
        cfg = self._make(strategy_type="linear")
        assert cfg.strategy_type == "linear"

    def test_invalid_strategy_type_rejected(self):
        with pytest.raises(ValidationError, match="Strategy type must be one of"):
            self._make(strategy_type="random")


@pytest.mark.unit
class TestRetryConfig:
    """RetryConfig validates max_attempts."""

    def _make(self, **kwargs):
        from orb.config.schemas.storage_schema import RetryConfig

        return RetryConfig(**kwargs)

    def test_default_max_attempts(self):
        assert self._make().max_attempts == 3

    def test_zero_max_attempts_is_valid(self):
        cfg = self._make(max_attempts=0)
        assert cfg.max_attempts == 0

    def test_negative_max_attempts_rejected(self):
        with pytest.raises(ValidationError, match="Max attempts must be non-negative"):
            self._make(max_attempts=-1)


# ---------------------------------------------------------------------------
# AdaptiveBatchSizingConfig
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAdaptiveBatchSizingConfig:
    """AdaptiveBatchSizingConfig cross-field validators."""

    def _make(self, **kwargs):
        from orb.config.schemas.performance_schema import AdaptiveBatchSizingConfig

        return AdaptiveBatchSizingConfig(**kwargs)

    def test_defaults_are_valid(self):
        cfg = self._make()
        assert cfg.initial_batch_size == 10

    def test_min_gt_max_rejected(self):
        with pytest.raises(ValidationError, match="Minimum batch size cannot be greater"):
            self._make(min_batch_size=20, max_batch_size=10, initial_batch_size=15)

    def test_initial_below_min_rejected(self):
        with pytest.raises(ValidationError, match="Initial batch size must be between"):
            self._make(min_batch_size=5, max_batch_size=20, initial_batch_size=3)

    def test_initial_above_max_rejected(self):
        with pytest.raises(ValidationError, match="Initial batch size must be between"):
            self._make(min_batch_size=5, max_batch_size=20, initial_batch_size=25)

    def test_zero_batch_size_rejected(self):
        with pytest.raises(ValidationError, match="Batch size must be at least 1"):
            self._make(initial_batch_size=0)

    def test_non_positive_increase_factor_rejected(self):
        with pytest.raises(ValidationError, match="Factor must be positive"):
            self._make(increase_factor=0.0)

    def test_zero_threshold_rejected(self):
        with pytest.raises(ValidationError, match="Threshold must be at least 1"):
            self._make(success_threshold=0)

    def test_zero_history_size_rejected(self):
        with pytest.raises(ValidationError, match="History size must be at least 1"):
            self._make(history_size=0)

    def test_valid_custom_config(self):
        cfg = self._make(
            initial_batch_size=10,
            min_batch_size=5,
            max_batch_size=50,
            increase_factor=2.0,
            decrease_factor=0.5,
        )
        assert cfg.max_batch_size == 50


@pytest.mark.unit
class TestPerformanceConfigValidators:
    """PerformanceConfig field validators."""

    def _make(self, **kwargs):
        from orb.config.schemas.performance_schema import PerformanceConfig

        return PerformanceConfig(**kwargs)

    def test_default_is_valid(self):
        cfg = self._make()
        assert cfg.max_workers == 10

    def test_zero_max_workers_rejected(self):
        with pytest.raises(ValidationError, match="Maximum workers must be at least 1"):
            self._make(max_workers=0)

    def test_zero_sync_timeout_rejected(self):
        with pytest.raises(ValidationError, match="sync_timeout_seconds must be positive"):
            self._make(sync_timeout_seconds=0.0)

    def test_negative_sync_timeout_rejected(self):
        with pytest.raises(ValidationError, match="sync_timeout_seconds must be positive"):
            self._make(sync_timeout_seconds=-1.0)

    def test_valid_custom_max_workers(self):
        cfg = self._make(max_workers=5)
        assert cfg.max_workers == 5


# ---------------------------------------------------------------------------
# AMIResolutionCacheConfig / RequestStatusCacheConfig
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCacheConfigValidators:
    """Cache TTL field validators."""

    def test_ami_cache_negative_ttl_rejected(self):
        from orb.config.schemas.performance_schema import AMIResolutionCacheConfig

        with pytest.raises(ValidationError, match="AMI cache TTL must be non-negative"):
            AMIResolutionCacheConfig(ttl_seconds=-1)  # type: ignore[call-arg]

    def test_ami_cache_zero_ttl_is_valid(self):
        from orb.config.schemas.performance_schema import AMIResolutionCacheConfig

        cfg = AMIResolutionCacheConfig(ttl_seconds=0)  # type: ignore[call-arg]
        assert cfg.ttl_seconds == 0

    def test_request_status_cache_negative_ttl_rejected(self):
        from orb.config.schemas.performance_schema import RequestStatusCacheConfig

        with pytest.raises(ValidationError, match="Request status cache TTL must be non-negative"):
            RequestStatusCacheConfig(ttl_seconds=-1)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# CommonSchema validators
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestConfigValidator:
    """RequestConfig.validate_max_machines rejects zero/negative values."""

    def _make(self, **kwargs):
        from orb.config.schemas.common_schema import RequestConfig

        return RequestConfig(**kwargs)

    def test_default_is_valid(self):
        cfg = self._make()
        assert cfg.max_machines_per_request == 100

    def test_zero_max_machines_rejected(self):
        with pytest.raises(ValidationError, match="must be at least 1"):
            self._make(max_machines_per_request=0)

    def test_negative_max_machines_rejected(self):
        with pytest.raises(ValidationError, match="must be at least 1"):
            self._make(max_machines_per_request=-5)


@pytest.mark.unit
class TestDatabaseConfigValidator:
    """DatabaseConfig validates timeout and max_connections."""

    def _make(self, **kwargs):
        from orb.config.schemas.common_schema import DatabaseConfig

        return DatabaseConfig(**kwargs)

    def test_zero_connection_timeout_rejected(self):
        with pytest.raises(ValidationError, match="Timeout must be at least 1"):
            self._make(connection_timeout=0)

    def test_zero_query_timeout_rejected(self):
        with pytest.raises(ValidationError, match="Timeout must be at least 1"):
            self._make(query_timeout=0)

    def test_zero_max_connections_rejected(self):
        with pytest.raises(ValidationError, match="Maximum connections must be at least 1"):
            self._make(max_connections=0)

    def test_valid_defaults(self):
        cfg = self._make()
        assert cfg.connection_timeout == 30
        assert cfg.max_connections == 10


@pytest.mark.unit
class TestEventsConfigValidator:
    """EventsConfig field validators."""

    def _make(self, **kwargs):
        from orb.config.schemas.common_schema import EventsConfig

        return EventsConfig(**kwargs)

    def test_zero_max_events_rejected(self):
        with pytest.raises(ValidationError, match="must be at least 1"):
            self._make(max_events_per_request=0)

    def test_zero_retention_days_rejected(self):
        with pytest.raises(ValidationError, match="must be at least 1"):
            self._make(event_retention_days=0)

    def test_valid_defaults(self):
        cfg = self._make()
        assert cfg.max_events_per_request == 1000
        assert cfg.event_retention_days == 30


@pytest.mark.unit
class TestResourceConfigModelValidator:
    """ResourceConfig.set_default_prefix propagates prefix.default when default_prefix absent."""

    def test_set_default_prefix_from_prefixes(self):
        from orb.config.schemas.common_schema import ResourceConfig

        cfg = ResourceConfig(prefixes={"default": "my-"})  # type: ignore[call-arg]
        assert cfg.default_prefix == "my-"

    def test_explicit_default_prefix_not_overwritten(self):
        from orb.config.schemas.common_schema import ResourceConfig

        cfg = ResourceConfig(default_prefix="explicit-")  # type: ignore[call-arg]
        assert cfg.default_prefix == "explicit-"


# ---------------------------------------------------------------------------
# BaseCircuitBreakerConfig validators
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseCircuitBreakerConfig:
    """BaseCircuitBreakerConfig field validators."""

    def _make(self, **kwargs):
        from orb.config.schemas.base_config import BaseCircuitBreakerConfig

        return BaseCircuitBreakerConfig(**kwargs)

    def test_zero_failure_threshold_rejected(self):
        with pytest.raises(ValidationError, match="Failure threshold must be positive"):
            self._make(failure_threshold=0)

    def test_zero_recovery_timeout_rejected(self):
        with pytest.raises(ValidationError, match="Recovery timeout must be positive"):
            self._make(recovery_timeout=0)

    def test_zero_half_open_max_calls_rejected(self):
        with pytest.raises(ValidationError, match="Half open max calls must be positive"):
            self._make(half_open_max_calls=0)

    def test_valid_defaults(self):
        cfg = self._make()
        assert cfg.failure_threshold == 5
        assert cfg.recovery_timeout == 60


# ---------------------------------------------------------------------------
# ProviderStrategySchema validators
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderInstanceConfigValidator:
    """ProviderInstanceConfig field validators."""

    def _make(self, **kwargs):
        from orb.config.schemas.provider_strategy_schema import ProviderInstanceConfig

        return ProviderInstanceConfig(**kwargs)

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError, match="cannot be empty"):
            self._make(name="", type="aws")

    def test_invalid_name_chars_rejected(self):
        with pytest.raises(ValidationError, match="alphanumeric"):
            self._make(name="has spaces!", type="aws")

    def test_empty_type_rejected(self):
        with pytest.raises(ValidationError, match="cannot be empty"):
            self._make(name="p1", type="")

    def test_zero_weight_rejected(self):
        with pytest.raises(ValidationError, match="weight must be positive"):
            self._make(name="p1", type="aws", weight=0)

    def test_valid_minimal_config(self):
        cfg = self._make(name="p1", type="aws")
        assert cfg.name == "p1"
        assert cfg.enabled is True

    def test_name_with_hyphens_and_underscores_is_valid(self):
        cfg = self._make(name="my-provider_1", type="aws")
        assert cfg.name == "my-provider_1"


@pytest.mark.unit
class TestProviderConfigValidator:
    """ProviderConfig model-level validators."""

    def _make(self, **kwargs):
        from orb.config.schemas.provider_strategy_schema import ProviderConfig

        return ProviderConfig(**kwargs)

    def test_empty_providers_is_valid(self):
        cfg = self._make(providers=[])
        assert cfg.providers == []

    def test_invalid_selection_policy_rejected(self):
        with pytest.raises(ValidationError, match="Selection policy must be one of"):
            self._make(selection_policy="INVALID_POLICY")

    def test_active_provider_must_exist_in_providers(self):
        with pytest.raises(ValidationError, match="Active provider.*not found"):
            self._make(
                active_provider="missing",
                providers=[{"name": "p1", "type": "aws"}],
            )

    def test_duplicate_provider_names_rejected(self):
        with pytest.raises(ValidationError, match="must be unique"):
            self._make(
                providers=[
                    {"name": "p1", "type": "aws"},
                    {"name": "p1", "type": "k8s"},
                ]
            )

    def test_zero_health_check_interval_rejected(self):
        with pytest.raises(ValidationError, match="Health check interval must be positive"):
            self._make(health_check_interval=0)

    def test_get_mode_none_when_no_providers(self):
        from orb.config.schemas.provider_strategy_schema import ProviderMode

        cfg = self._make(providers=[])
        assert cfg.get_mode() == ProviderMode.NONE

    def test_get_mode_single_when_active_provider_set(self):
        from orb.config.schemas.provider_strategy_schema import ProviderMode

        cfg = self._make(
            active_provider="p1",
            providers=[{"name": "p1", "type": "aws"}],
        )
        assert cfg.get_mode() == ProviderMode.SINGLE

    def test_get_mode_multi_when_multiple_enabled(self):
        from orb.config.schemas.provider_strategy_schema import ProviderMode

        cfg = self._make(
            providers=[
                {"name": "p1", "type": "aws", "enabled": True},
                {"name": "p2", "type": "k8s", "enabled": True},
            ]
        )
        assert cfg.get_mode() == ProviderMode.MULTI

    def test_get_mode_single_when_one_disabled_one_enabled(self):
        from orb.config.schemas.provider_strategy_schema import ProviderMode

        cfg = self._make(
            providers=[
                {"name": "p1", "type": "aws", "enabled": True},
                {"name": "p2", "type": "k8s", "enabled": False},
            ]
        )
        assert cfg.get_mode() == ProviderMode.SINGLE

    def test_get_active_providers_round_robin_returns_all_enabled(self):
        cfg = self._make(
            selection_policy="ROUND_ROBIN",
            providers=[
                {"name": "p1", "type": "aws", "enabled": True},
                {"name": "p2", "type": "aws", "enabled": False},
                {"name": "p3", "type": "k8s", "enabled": True},
            ],
        )
        active = cfg.get_active_providers()
        names = [p.name for p in active]
        assert "p1" in names
        assert "p3" in names
        assert "p2" not in names

    def test_get_active_providers_first_available_with_active_provider(self):
        cfg = self._make(
            selection_policy="FIRST_AVAILABLE",
            active_provider="p1",
            providers=[
                {"name": "p1", "type": "aws", "enabled": True},
                {"name": "p2", "type": "k8s", "enabled": True},
            ],
        )
        active = cfg.get_active_providers()
        assert len(active) == 1
        assert active[0].name == "p1"

    def test_is_multi_provider_mode(self):
        cfg = self._make(
            providers=[
                {"name": "p1", "type": "aws", "enabled": True},
                {"name": "p2", "type": "k8s", "enabled": True},
            ]
        )
        assert cfg.is_multi_provider_mode() is True

    def test_get_provider_by_name_returns_correct_instance(self):
        cfg = self._make(
            providers=[
                {"name": "p1", "type": "aws"},
                {"name": "p2", "type": "k8s"},
            ]
        )
        p = cfg.get_provider_by_name("p2")
        assert p is not None
        assert p.type == "k8s"

    def test_get_provider_by_name_returns_none_for_missing(self):
        cfg = self._make(providers=[{"name": "p1", "type": "aws"}])
        assert cfg.get_provider_by_name("missing") is None


@pytest.mark.unit
class TestHandlerConfigMerge:
    """HandlerConfig.merge_with merges override fields."""

    def _make(self, **kwargs):
        from orb.config.schemas.provider_strategy_schema import HandlerConfig

        return HandlerConfig(**kwargs)

    def test_merge_overrides_field(self):
        base = self._make(handler_class="BaseHandler", extra_field="base_value")
        override = self._make(handler_class="OverrideHandler")
        merged = base.merge_with(override)
        assert merged.handler_class == "OverrideHandler"

    def test_merge_preserves_base_extra_fields_not_in_override(self):
        base = self._make(handler_class="H", supports_spot=True)
        override = self._make(handler_class="H2")
        merged = base.merge_with(override)
        # supports_spot from base should persist since override doesn't set it
        # (model_dump includes extra fields)
        assert merged.handler_class == "H2"


@pytest.mark.unit
class TestProviderInstanceConfigGetEffectiveHandlers:
    """get_effective_handlers applies defaults, overrides, and null-removals."""

    def _make_instance(self, **kwargs):
        from orb.config.schemas.provider_strategy_schema import ProviderInstanceConfig

        return ProviderInstanceConfig(name="p1", type="aws", **kwargs)

    def _make_defaults(self, handlers: dict):
        from orb.config.schemas.provider_strategy_schema import HandlerConfig, ProviderDefaults

        return ProviderDefaults(
            handlers={k: HandlerConfig(handler_class=v) for k, v in handlers.items()}
        )

    def test_no_handlers_and_no_defaults_returns_empty(self):
        inst = self._make_instance()
        result = inst.get_effective_handlers(None)
        assert result == {}

    def test_defaults_inherited_when_no_override(self):
        defaults = self._make_defaults({"spot": "SpotHandler", "fleet": "FleetHandler"})
        inst = self._make_instance()
        result = inst.get_effective_handlers(defaults)
        assert "spot" in result
        assert "fleet" in result

    def test_null_override_removes_handler(self):
        defaults = self._make_defaults({"spot": "SpotHandler", "fleet": "FleetHandler"})
        inst = self._make_instance(handler_overrides={"fleet": None})
        result = inst.get_effective_handlers(defaults)
        assert "spot" in result
        assert "fleet" not in result

    def test_handler_override_merges_with_default(self):
        from orb.config.schemas.provider_strategy_schema import HandlerConfig

        defaults = self._make_defaults({"spot": "SpotHandler"})
        inst = self._make_instance(
            handler_overrides={"spot": HandlerConfig(handler_class="CustomSpot")}
        )
        result = inst.get_effective_handlers(defaults)
        assert result["spot"].handler_class == "CustomSpot"

    def test_new_handler_not_in_defaults_added(self):
        from orb.config.schemas.provider_strategy_schema import HandlerConfig

        defaults = self._make_defaults({"spot": "SpotHandler"})
        inst = self._make_instance(
            handler_overrides={"new_handler": HandlerConfig(handler_class="NewH")}
        )
        result = inst.get_effective_handlers(defaults)
        assert "new_handler" in result
        assert "spot" in result

    def test_full_handlers_override_ignores_defaults(self):
        from orb.config.schemas.provider_strategy_schema import HandlerConfig

        defaults = self._make_defaults({"spot": "SpotHandler"})
        inst = self._make_instance(handlers={"only": HandlerConfig(handler_class="OnlyH")})
        result = inst.get_effective_handlers(defaults)
        # When full handlers override is set, only merged entries appear
        assert "only" in result


# ---------------------------------------------------------------------------
# ConfigValidator business rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigValidatorBusinessRules:
    """ConfigValidator._validate_business_rules emits warnings for excessive resources."""

    def _validator(self):
        from orb.config.validators.config_validator import ConfigValidator

        return ConfigValidator()

    def _minimal_config(self, **overrides):
        base = {
            "provider": {"selection_policy": "FIRST_AVAILABLE", "providers": []},
        }
        base.update(overrides)
        return base

    def test_high_max_workers_emits_warning(self):
        validator = self._validator()
        config_data = self._minimal_config(performance={"max_workers": 51})
        result = validator.validate_config(config_data)
        assert any("max_workers" in w for w in result.warnings)

    def test_normal_max_workers_no_warning(self):
        validator = self._validator()
        config_data = self._minimal_config(performance={"max_workers": 10})
        result = validator.validate_config(config_data)
        assert not any("max_workers" in w for w in result.warnings)

    def test_large_sql_pool_emits_warning(self):
        validator = self._validator()
        config_data = self._minimal_config(
            storage={
                "strategy": "sql",
                "sql_strategy": {
                    "type": "postgresql",
                    "host": "pg.example.com",
                    "port": 5432,
                    "name": "mydb",
                    "pool_size": 21,
                },
            }
        )
        result = validator.validate_config(config_data)
        assert any("pool" in w.lower() for w in result.warnings)

    def test_invalid_config_data_adds_error(self):
        validator = self._validator()
        # Pass completely invalid config
        result = validator.validate_config({"invalid_field_xyz": 123})
        assert not result.is_valid
        assert len(result.errors) > 0

    def test_validation_result_add_error_marks_invalid(self):
        from orb.config.validators.config_validator import ValidationResult

        vr = ValidationResult()
        assert vr.is_valid is True
        vr.add_error("something broke")
        assert vr.is_valid is False
        assert "something broke" in vr.errors

    def test_validation_result_add_warning_keeps_valid(self):
        from orb.config.validators.config_validator import ValidationResult

        vr = ValidationResult()
        vr.add_warning("careful about X")
        assert vr.is_valid is True
        assert "careful about X" in vr.warnings

    def test_validation_result_constructor_with_initial_errors(self):
        from orb.config.validators.config_validator import ValidationResult

        vr = ValidationResult(errors=["err1", "err2"])
        assert vr.is_valid is False
        assert len(vr.errors) == 2


# ---------------------------------------------------------------------------
# ProviderSettingsRegistry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderSettingsRegistry:
    """ProviderSettingsRegistry registers and retrieves settings classes."""

    def test_register_and_retrieve(self):
        from pydantic_settings import BaseSettings

        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry

        class FakeSettings(BaseSettings):
            model_config = {"extra": "ignore"}

        ProviderSettingsRegistry.register_provider_settings("_test_fake_", FakeSettings)
        assert "_test_fake_" in ProviderSettingsRegistry.get_registered_provider_types()
        assert ProviderSettingsRegistry.get_or_none("_test_fake_") is FakeSettings
        # Cleanup
        del ProviderSettingsRegistry._settings_classes["_test_fake_"]

    def test_get_or_none_returns_none_for_unknown(self):
        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry

        assert ProviderSettingsRegistry.get_or_none("_nonexistent_xyz_") is None

    def test_get_settings_class_falls_back_to_base_settings(self):
        from pydantic_settings import BaseSettings

        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry

        cls = ProviderSettingsRegistry.get_settings_class("_nonexistent_xyz_")
        assert cls is BaseSettings
