"""Unit tests for request metadata value objects."""

from datetime import datetime, timedelta

import pytest

from orb.domain.request.request_metadata import (
    LaunchTemplateInfo,
    MachineCount,
    RequestConfiguration,
    RequestHistoryEvent,
    RequestTag,
    RequestTimeout,
)


class TestRequestTimeout:
    def test_valid_timeout(self):
        t = RequestTimeout(seconds=300)
        assert t.seconds == 300

    def test_zero_timeout_raises(self):
        with pytest.raises(Exception):
            RequestTimeout(seconds=0)

    def test_negative_timeout_raises(self):
        with pytest.raises(Exception):
            RequestTimeout(seconds=-1)

    def test_exceeds_max_raises(self):
        with pytest.raises(Exception):
            RequestTimeout(seconds=86401)  # > 1 day

    def test_duration_property(self):
        t = RequestTimeout(seconds=60)
        assert t.duration == timedelta(seconds=60)

    def test_expiry_time_is_future(self):
        t = RequestTimeout(seconds=3600)
        assert t.expiry_time > datetime.utcnow()

    def test_is_expired_false_for_recent_start(self):
        t = RequestTimeout(seconds=3600)
        assert t.is_expired(datetime.utcnow()) is False

    def test_is_expired_true_for_old_start(self):
        t = RequestTimeout(seconds=1)
        old_start = datetime.utcnow() - timedelta(seconds=10)
        assert t.is_expired(old_start) is True

    def test_from_seconds(self):
        t = RequestTimeout.from_seconds(120)
        assert t.seconds == 120

    def test_default_creates_instance(self):
        t = RequestTimeout.default()
        assert t.seconds > 0


class TestMachineCount:
    def test_valid_count(self):
        mc = MachineCount(value=5)
        assert mc.value == 5

    def test_zero_raises(self):
        with pytest.raises(Exception):
            MachineCount(value=0)

    def test_negative_raises(self):
        with pytest.raises(Exception):
            MachineCount(value=-1)

    def test_exceeds_max_raises(self):
        with pytest.raises(Exception):
            MachineCount(value=101, max_allowed=100)

    def test_str_representation(self):
        mc = MachineCount(value=3)
        assert str(mc) == "3"

    def test_int_representation(self):
        mc = MachineCount(value=7)
        assert int(mc) == 7

    def test_from_int(self):
        mc = MachineCount.from_int(10, max_allowed=50)
        assert mc.value == 10

    def test_explicit_max_allowed(self):
        mc = MachineCount(value=50, max_allowed=50)
        assert mc.value == 50

    def test_exceeds_explicit_max_raises(self):
        with pytest.raises(Exception):
            MachineCount(value=51, max_allowed=50)


class TestRequestTag:
    def test_valid_tag(self):
        tag = RequestTag(key="env", value="prod")
        assert tag.key == "env"
        assert tag.value == "prod"

    def test_str_representation(self):
        tag = RequestTag(key="env", value="prod")
        assert str(tag) == "env=prod"

    def test_empty_key_raises(self):
        with pytest.raises(Exception):
            RequestTag(key="", value="prod")

    def test_key_too_long_raises(self):
        with pytest.raises(Exception):
            RequestTag(key="x" * 129, value="prod")

    def test_value_too_long_raises(self):
        with pytest.raises(Exception):
            RequestTag(key="env", value="x" * 257)

    def test_whitespace_key_stripped(self):
        tag = RequestTag(key="  env  ", value="prod")
        assert tag.key == "env"

    def test_from_string_valid(self):
        tag = RequestTag.from_string("env=prod")
        assert tag.key == "env"
        assert tag.value == "prod"

    def test_from_string_with_equals_in_value(self):
        tag = RequestTag.from_string("key=val=ue")
        assert tag.key == "key"
        assert tag.value == "val=ue"

    def test_from_string_no_equals_raises(self):
        with pytest.raises(ValueError, match="key=value"):
            RequestTag.from_string("no-equals-here")


class TestRequestConfiguration:
    def test_valid_config(self):
        cfg = RequestConfiguration(template_id="tmpl-1", machine_count=2)
        assert cfg.template_id == "tmpl-1"
        assert cfg.machine_count == 2
        assert cfg.timeout == 3600

    def test_empty_template_id_raises(self):
        with pytest.raises(Exception):
            RequestConfiguration(template_id="", machine_count=1)

    def test_zero_machine_count_raises(self):
        with pytest.raises(Exception):
            RequestConfiguration(template_id="tmpl-1", machine_count=0)

    def test_negative_machine_count_raises(self):
        with pytest.raises(Exception):
            RequestConfiguration(template_id="tmpl-1", machine_count=-1)

    def test_zero_timeout_raises(self):
        with pytest.raises(Exception):
            RequestConfiguration(template_id="tmpl-1", machine_count=1, timeout=0)

    def test_add_tag_returns_new_instance(self):
        cfg = RequestConfiguration(template_id="tmpl-1", machine_count=1)
        cfg2 = cfg.add_tag("env", "prod")
        assert cfg2.tags["env"] == "prod"
        assert "env" not in cfg.tags  # original unchanged

    def test_with_provider_config_returns_new_instance(self):
        cfg = RequestConfiguration(template_id="tmpl-1", machine_count=1)
        cfg2 = cfg.with_provider_config({"region": "us-east-1"})
        assert cfg2.provider_config["region"] == "us-east-1"
        assert cfg.provider_config == {}

    def test_get_timeout_object(self):
        cfg = RequestConfiguration(template_id="tmpl-1", machine_count=1, timeout=600)
        t = cfg.get_timeout_object()
        assert t.seconds == 600

    def test_get_machine_count_object(self):
        cfg = RequestConfiguration(template_id="tmpl-1", machine_count=3)
        mc = cfg.get_machine_count_object()
        assert mc.value == 3

    def test_get_tags_list(self):
        cfg = RequestConfiguration(template_id="tmpl-1", machine_count=1, tags={"a": "1", "b": "2"})
        tags = cfg.get_tags_list()
        assert len(tags) == 2
        keys = {t.key for t in tags}
        assert keys == {"a", "b"}


class TestLaunchTemplateInfo:
    def test_valid(self):
        lt = LaunchTemplateInfo(template_id="lt-123")
        assert lt.template_id == "lt-123"
        assert lt.version == "$Latest"

    def test_empty_template_id_raises(self):
        with pytest.raises(Exception):
            LaunchTemplateInfo(template_id="")

    def test_is_latest_version_default(self):
        lt = LaunchTemplateInfo(template_id="lt-123")
        assert lt.is_latest_version() is True

    def test_is_latest_version_explicit(self):
        lt = LaunchTemplateInfo(template_id="lt-123", version="latest")
        assert lt.is_latest_version() is True

    def test_is_not_latest_version(self):
        lt = LaunchTemplateInfo(template_id="lt-123", version="5")
        assert lt.is_latest_version() is False

    def test_get_display_name_uses_name(self):
        lt = LaunchTemplateInfo(template_id="lt-123", template_name="my-template")
        assert lt.get_display_name() == "my-template"

    def test_get_display_name_falls_back_to_id(self):
        lt = LaunchTemplateInfo(template_id="lt-123")
        assert lt.get_display_name() == "lt-123"

    def test_str_with_name(self):
        lt = LaunchTemplateInfo(template_id="lt-123", template_name="my-tmpl", version="3")
        assert "lt-123" in str(lt)
        assert "my-tmpl" in str(lt)
        assert "3" in str(lt)


class TestRequestHistoryEvent:
    def test_valid_event(self):
        evt = RequestHistoryEvent(
            event_type="status_change",
            timestamp="2026-01-01T00:00:00",
            message="Status changed",
        )
        assert evt.event_type == "status_change"
        assert evt.message == "Status changed"

    def test_event_type_normalized(self):
        evt = RequestHistoryEvent(
            event_type="Status-Change",
            timestamp="2026-01-01T00:00:00",
            message="msg",
        )
        assert evt.event_type == "status_change"

    def test_invalid_timestamp_raises(self):
        with pytest.raises(Exception):
            RequestHistoryEvent(
                event_type="error",
                timestamp="not-a-date",
                message="msg",
            )

    def test_empty_message_raises(self):
        with pytest.raises(Exception):
            RequestHistoryEvent(
                event_type="error",
                timestamp="2026-01-01T00:00:00",
                message="",
            )

    def test_create_factory(self):
        evt = RequestHistoryEvent.create("error", "Something went wrong", source="provider")
        assert evt.event_type == "error"
        assert evt.message == "Something went wrong"
        assert evt.source == "provider"

    def test_is_error_event(self):
        evt = RequestHistoryEvent.create("error", "msg")
        assert evt.is_error_event() is True

    def test_is_not_error_event(self):
        evt = RequestHistoryEvent.create("status_change", "msg")
        assert evt.is_error_event() is False

    def test_is_status_change_event(self):
        evt = RequestHistoryEvent.create("status_change", "msg")
        assert evt.is_status_change_event() is True

    def test_str_representation(self):
        evt = RequestHistoryEvent.create("error", "Something failed")
        s = str(evt)
        assert "error" in s
        assert "Something failed" in s
