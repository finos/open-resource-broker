"""Regression tests: ORB's own bundled AWS defaults must not trip deprecations.

The template field rename (``instance_type`` -> ``machine_type``, ``image_id``
-> ``machine_image``, ``max_instances`` -> ``max_machines``, ``key_name`` ->
``machine_ssh_key``) added operator-facing ``logger.warning`` messages for the
old field names.  ORB's own shipped config and example templates must use the
new canonical names so ``orb init`` never warns about its own bundled files.

These tests assert:
  * the shipped ``aws_defaults.json`` ``template_defaults`` block uses only the
    canonical ``machine_*`` names and loads into an ``AWSTemplate`` with no
    operator-facing deprecation log message; and
  * the bundled example templates (built at import time) emit no operator-facing
    deprecation log message.

The deprecation-warning code itself is intentionally left intact — external
users passing old field names must still be warned.
"""

from __future__ import annotations

import logging

import pytest

TEMPLATE_LOGGER = "orb.domain.template.template_aggregate"

# Genuine Template fields that carry deprecation warnings when the OLD name is
# used.  (Handler-capability ``max_instances``, the ``naming.instance_type``
# regex pattern name, and the AWSTemplateExtensionConfig ``volume_type`` /
# ``root_device_volume_size`` fields are NOT template fields and are excluded.)
_DEPRECATED_TEMPLATE_KEYS = {
    "instance_type",
    "image_id",
    "max_instances",
    "key_name",
    "user_data",
    "instance_profile",
}


class _CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _capture_template_deprecation_logs():
    handler = _CapturingHandler()
    logger = logging.getLogger(TEMPLATE_LOGGER)
    logger.addHandler(handler)
    prev_level = logger.level
    logger.setLevel(logging.WARNING)
    return logger, handler, prev_level


@pytest.mark.unit
class TestShippedAWSDefaults:
    """The bundled aws_defaults.json must use canonical machine_* field names."""

    def test_template_defaults_use_canonical_field_names(self) -> None:
        from orb.providers.aws.defaults_loader import AWSDefaultsLoader

        defaults = AWSDefaultsLoader().load_defaults()
        template_defaults = defaults["provider"]["provider_defaults"]["aws"]["template_defaults"]

        offending = _DEPRECATED_TEMPLATE_KEYS & set(template_defaults)
        assert not offending, (
            f"aws_defaults.json template_defaults uses deprecated keys: {offending}"
        )

        # Canonical names must be present in place of the renamed ones.
        assert "machine_image" in template_defaults
        assert "machine_type" in template_defaults
        assert "machine_ssh_key" in template_defaults

    def test_template_defaults_load_without_deprecation_log(self) -> None:
        from orb.providers.aws.defaults_loader import AWSDefaultsLoader
        from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate

        defaults = AWSDefaultsLoader().load_defaults()
        template_defaults = defaults["provider"]["provider_defaults"]["aws"]["template_defaults"]

        logger, handler, prev_level = _capture_template_deprecation_logs()
        try:
            AWSTemplate(template_id="t", name="t", **template_defaults)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prev_level)

        deprecation_msgs = [m for m in handler.messages if "is deprecated" in m]
        assert not deprecation_msgs, (
            f"Shipped aws_defaults template_defaults emitted deprecation warnings: "
            f"{deprecation_msgs}"
        )


@pytest.mark.unit
class TestShippedExampleTemplates:
    """The bundled example templates must use canonical machine_* field names."""

    def test_ec2_fleet_and_microvm_examples_no_deprecation_log(self) -> None:
        logger, handler, prev_level = _capture_template_deprecation_logs()
        try:
            # Rebuild the catalogues under the log capture so any deprecated
            # kwarg would surface as an operator-facing warning.
            from orb.providers.aws.infrastructure.handlers.ec2_fleet.example_templates import (
                build_ec2_fleet_example_templates,
            )
            from orb.providers.aws.infrastructure.handlers.microvm.example_templates import (
                build_microvm_example_templates,
            )

            build_ec2_fleet_example_templates()
            build_microvm_example_templates()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prev_level)

        deprecation_msgs = [m for m in handler.messages if "is deprecated" in m]
        assert not deprecation_msgs, (
            f"Bundled example templates emitted deprecation warnings: {deprecation_msgs}"
        )

    def test_handler_example_templates_no_deprecation_log(self) -> None:
        logger, handler, prev_level = _capture_template_deprecation_logs()
        try:
            from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
            from orb.providers.aws.infrastructure.handlers.run_instances.handler import (
                RunInstancesHandler,
            )
            from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import (
                SpotFleetHandler,
            )

            ASGHandler.get_example_templates()
            SpotFleetHandler.get_example_templates()
            RunInstancesHandler.get_example_templates()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prev_level)

        deprecation_msgs = [m for m in handler.messages if "is deprecated" in m]
        assert not deprecation_msgs, (
            f"Handler example templates emitted deprecation warnings: {deprecation_msgs}"
        )
