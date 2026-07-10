"""Tests for OtelConfig observability schema."""

import os
from unittest.mock import patch

import pytest

from orb.config.schemas.observability_schema import OtelConfig


class TestOtelConfigDefaults:
    """OtelConfig must default to fully disabled."""

    def test_enabled_defaults_to_false(self):
        cfg = OtelConfig()
        assert cfg.enabled is False

    def test_metrics_exporters_defaults_to_empty(self):
        cfg = OtelConfig()
        assert cfg.metrics_exporters == []

    def test_traces_exporter_defaults_to_none(self):
        cfg = OtelConfig()
        assert cfg.traces_exporter is None

    def test_otlp_endpoint_defaults_to_none(self):
        cfg = OtelConfig()
        assert cfg.otlp_endpoint is None

    def test_service_name_defaults_to_orb(self):
        cfg = OtelConfig()
        assert cfg.service_name == "orb"

    def test_traces_sample_rate_defaults_to_point_one(self):
        cfg = OtelConfig()
        assert cfg.traces_sample_rate == 0.1


class TestOtelConfigEnvOverrides:
    """OTEL_* environment variables must override file-level values."""

    def test_otel_sdk_disabled_true_sets_enabled_false(self):
        """OTEL_SDK_DISABLED=true must disable OTel even when file says enabled."""
        with patch.dict(os.environ, {"OTEL_SDK_DISABLED": "true"}):
            cfg = OtelConfig(enabled=True)
        assert cfg.enabled is False

    def test_otel_sdk_disabled_case_insensitive(self):
        with patch.dict(os.environ, {"OTEL_SDK_DISABLED": "TRUE"}):
            cfg = OtelConfig(enabled=True)
        assert cfg.enabled is False

    def test_otel_sdk_disabled_false_does_not_override(self):
        """OTEL_SDK_DISABLED=false must NOT disable OTel."""
        with patch.dict(os.environ, {"OTEL_SDK_DISABLED": "false"}):
            cfg = OtelConfig(enabled=True)
        assert cfg.enabled is True

    def test_otel_exporter_otlp_endpoint_overrides_file_value(self):
        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4317"}):
            cfg = OtelConfig(otlp_endpoint="http://localhost:4317")
        assert cfg.otlp_endpoint == "http://otel-collector:4317"

    def test_otel_exporter_otlp_endpoint_sets_value_when_not_in_file(self):
        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4317"}):
            cfg = OtelConfig()
        assert cfg.otlp_endpoint == "http://otel-collector:4317"

    def test_otel_service_name_overrides_file_value(self):
        with patch.dict(os.environ, {"OTEL_SERVICE_NAME": "my-service"}):
            cfg = OtelConfig(service_name="orb")
        assert cfg.service_name == "my-service"

    def test_otel_traces_sampler_arg_overrides_file_value(self):
        with patch.dict(os.environ, {"OTEL_TRACES_SAMPLER_ARG": "0.5"}):
            cfg = OtelConfig(traces_sample_rate=0.1)
        assert cfg.traces_sample_rate == 0.5

    def test_otel_traces_sampler_arg_clamped_to_one(self):
        with patch.dict(os.environ, {"OTEL_TRACES_SAMPLER_ARG": "2.0"}):
            cfg = OtelConfig()
        assert cfg.traces_sample_rate == 1.0

    def test_otel_traces_sampler_arg_clamped_to_zero(self):
        with patch.dict(os.environ, {"OTEL_TRACES_SAMPLER_ARG": "-0.5"}):
            cfg = OtelConfig()
        assert cfg.traces_sample_rate == 0.0

    def test_otel_traces_sampler_arg_invalid_value_ignored(self):
        """Non-numeric OTEL_TRACES_SAMPLER_ARG must be silently ignored."""
        with patch.dict(os.environ, {"OTEL_TRACES_SAMPLER_ARG": "not-a-number"}):
            cfg = OtelConfig(traces_sample_rate=0.25)
        assert cfg.traces_sample_rate == 0.25

    def test_no_env_vars_leaves_file_values_unchanged(self):
        """When no OTEL_* env vars are set the file values must survive."""
        env_without_otel = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
        with patch.dict(os.environ, env_without_otel, clear=True):
            cfg = OtelConfig(
                enabled=True,
                service_name="custom",
                otlp_endpoint="http://x:4317",
                traces_sample_rate=0.75,
            )
        assert cfg.enabled is True
        assert cfg.service_name == "custom"
        assert cfg.otlp_endpoint == "http://x:4317"
        assert cfg.traces_sample_rate == 0.75


class TestOtelConfigValidation:
    """OtelConfig field validation."""

    def test_multiple_metrics_exporters_accepted(self):
        cfg = OtelConfig(metrics_exporters=["prometheus", "otlp"])
        assert cfg.metrics_exporters == ["prometheus", "otlp"]

    def test_sample_rate_boundary_zero_accepted(self):
        cfg = OtelConfig(traces_sample_rate=0.0)
        assert cfg.traces_sample_rate == 0.0

    def test_sample_rate_boundary_one_accepted(self):
        cfg = OtelConfig(traces_sample_rate=1.0)
        assert cfg.traces_sample_rate == 1.0

    def test_sample_rate_out_of_bounds_raises(self):
        with pytest.raises(Exception):
            OtelConfig(traces_sample_rate=1.1)
