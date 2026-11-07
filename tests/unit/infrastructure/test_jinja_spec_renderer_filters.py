"""Filters the production spec renderer provides to shipped spec templates."""

import base64
from pathlib import Path
from unittest.mock import MagicMock

import orb.providers.aws
from orb.infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer

_LAUNCH_TEMPLATE_DEFAULT_SPEC = (
    Path(orb.providers.aws.__file__).parent / "specs" / "launch-template" / "default.json"
)


def _default_spec_context(**overrides):
    context = {
        "image_id": "ami-12345",
        "instance_type": "t3.micro",
        "has_subnet": False,
        "has_security_groups": False,
        "has_key_name": False,
        "has_user_data": False,
        "has_instance_profile": False,
        "has_ebs_optimized": False,
        "has_monitoring": False,
        "has_root_device_volume_size": False,
        "has_iops": False,
        "has_custom_tags": False,
        "instance_name": "orb-instance",
        "request_id": "req-123",
        "template_id": "tmpl-1",
        "created_by": "orb",
    }
    context.update(overrides)
    return context


def test_shipped_launch_template_spec_renders_user_data_as_base64():
    """The shipped launch-template spec encodes user data without a test-local filter."""
    renderer = JinjaSpecRenderer(MagicMock())
    user_data = "#!/bin/bash\necho hi & <x>"

    out = renderer.render_spec_from_file(
        str(_LAUNCH_TEMPLATE_DEFAULT_SPEC),
        _default_spec_context(has_user_data=True, user_data=user_data),
    )

    assert base64.b64decode(out["UserData"]).decode("utf-8") == user_data


def test_b64encode_filter_in_inline_spec():
    renderer = JinjaSpecRenderer(MagicMock())

    out = renderer.render_spec({"u": "{{ s | b64encode }}"}, {"s": "abc"})

    assert out == {"u": "YWJj"}
