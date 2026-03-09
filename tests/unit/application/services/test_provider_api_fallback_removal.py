"""Tests asserting removal of AWS API name fallbacks from application services.

These tests enforce that:
- No 'RunInstances' or 'EC2Fleet' string literals exist in the 4 application service files
- RequestCreationService raises ValueError when template.provider_api is None
- MachineGroupingService logs a warning and skips machines with no provider_api
"""

import ast
import re
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Paths to the 4 application service files under test
_SRC = Path(__file__).parents[4] / "src" / "orb" / "application" / "services"
_FILES = [
    _SRC / "machine_grouping_service.py",
    _SRC / "machine_sync_service.py",
    _SRC / "request_creation_service.py",
    _SRC / "template_defaults_service.py",
]

# ---------------------------------------------------------------------------
# Static analysis: no AWS API name literals in application services
# ---------------------------------------------------------------------------


class TestNoAwsApiLiteralsInApplicationServices:
    """Scan source files for forbidden AWS API name string literals."""

    @pytest.mark.parametrize("service_file", _FILES, ids=[f.name for f in _FILES])
    def test_no_run_instances_literal(self, service_file: Path):
        source = service_file.read_text()
        # Match the string literal in any quote style
        matches = re.findall(r"""['"]RunInstances['"]""", source)
        assert matches == [], f"{service_file.name} contains 'RunInstances' literal(s): {matches}"

    @pytest.mark.parametrize("service_file", _FILES, ids=[f.name for f in _FILES])
    def test_no_ec2fleet_literal(self, service_file: Path):
        source = service_file.read_text()
        matches = re.findall(r"""['"]EC2Fleet['"]""", source)
        assert matches == [], f"{service_file.name} contains 'EC2Fleet' literal(s): {matches}"

    @pytest.mark.parametrize("service_file", _FILES, ids=[f.name for f in _FILES])
    def test_files_are_valid_python(self, service_file: Path):
        """Sanity check: files must still parse cleanly."""
        source = service_file.read_text()
        ast.parse(source)  # raises SyntaxError if broken


# ---------------------------------------------------------------------------
# RequestCreationService: raises ValueError when template.provider_api is None
# ---------------------------------------------------------------------------


class TestRequestCreationServiceRaisesOnMissingProviderApi:
    def setup_method(self):
        from orb.application.services.request_creation_service import RequestCreationService

        self.logger = MagicMock()
        self.svc = RequestCreationService(logger=self.logger)

    def _make_command(self, template_id="tmpl-001", requested_count=1):
        cmd = MagicMock()
        cmd.template_id = template_id
        cmd.requested_count = requested_count
        cmd.request_id = None
        cmd.metadata = {}
        cmd.dry_run = False
        return cmd

    def _make_selection(self):
        r = MagicMock()
        r.provider_type = "aws"
        r.provider_name = "aws-prod"
        r.selection_reason = "only provider"
        r.confidence = 1.0
        return r

    def test_raises_value_error_when_provider_api_is_none(self):
        template = MagicMock()
        template.provider_api = None
        template.template_id = "tmpl-missing-api"

        fake_request = MagicMock()
        fake_request.request_id = "req-abc"

        with patch("orb.application.services.request_creation_service.Request") as MockRequest:
            MockRequest.create_new_request.return_value = fake_request
            with pytest.raises(ValueError, match="tmpl-missing-api"):
                self.svc.create_machine_request(
                    self._make_command(), template, self._make_selection()
                )

    def test_raises_value_error_when_provider_api_is_empty_string(self):
        template = MagicMock()
        template.provider_api = ""
        template.template_id = "tmpl-empty-api"

        fake_request = MagicMock()
        fake_request.request_id = "req-abc"

        with patch("orb.application.services.request_creation_service.Request") as MockRequest:
            MockRequest.create_new_request.return_value = fake_request
            with pytest.raises(ValueError, match="tmpl-empty-api"):
                self.svc.create_machine_request(
                    self._make_command(), template, self._make_selection()
                )

    def test_succeeds_when_provider_api_is_set(self):
        template = MagicMock()
        template.provider_api = "RunInstances"
        template.template_id = "tmpl-ok"

        fake_request = MagicMock()
        fake_request.request_id = "req-abc"
        fake_request.provider_api = None

        with patch("orb.application.services.request_creation_service.Request") as MockRequest:
            MockRequest.create_new_request.return_value = fake_request
            result = self.svc.create_machine_request(
                self._make_command(), template, self._make_selection()
            )

        assert result.provider_api == "RunInstances"


# ---------------------------------------------------------------------------
# MachineGroupingService: warns and skips machines with no provider_api
# ---------------------------------------------------------------------------


class TestMachineGroupingServiceSkipsMissingProviderApi:
    def setup_method(self):
        from orb.application.services.machine_grouping_service import MachineGroupingService

        self.logger = MagicMock()
        self.uow_factory = MagicMock()
        self.svc = MachineGroupingService(uow_factory=self.uow_factory, logger=self.logger)

    def _make_machine(self, machine_id, provider_name, provider_api, resource_id="res-1"):
        m = MagicMock()
        m.machine_id.value = machine_id
        m.provider_name = provider_name
        m.provider_api = provider_api
        m.resource_id = resource_id
        return m

    def _setup_uow(self, machines_by_id: dict):
        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow.machines.find_by_id.side_effect = lambda mid: machines_by_id.get(mid)
        self.uow_factory.create_unit_of_work.return_value = uow

    def test_machine_with_no_provider_api_is_skipped(self):
        m1 = self._make_machine("i-aaa", "aws-prod", "RunInstances", "asg-1")
        m2 = self._make_machine("i-bbb", "aws-prod", None, "asg-1")  # no provider_api

        self._setup_uow({"i-aaa": m1, "i-bbb": m2})

        result = self.svc.group_by_resource(["i-aaa", "i-bbb"])

        # i-bbb must be absent from the result
        all_machines = [m for machines in result.values() for m in machines]
        machine_ids = [m.machine_id.value for m in all_machines]
        assert "i-bbb" not in machine_ids

    def test_machine_with_no_provider_api_triggers_warning_log(self):
        m = self._make_machine("i-bbb", "aws-prod", None, "asg-1")
        self._setup_uow({"i-bbb": m})

        self.svc.group_by_resource(["i-bbb"])

        # A warning must have been logged
        assert self.logger.warning.called

    def test_machine_with_no_provider_api_does_not_raise(self):
        m = self._make_machine("i-bbb", "aws-prod", None, "asg-1")
        self._setup_uow({"i-bbb": m})

        # Must not raise — legacy rows should be skipped gracefully
        result = self.svc.group_by_resource(["i-bbb"])
        assert isinstance(result, dict)

    def test_valid_machines_still_grouped_when_some_skipped(self):
        m1 = self._make_machine("i-aaa", "aws-prod", "RunInstances", "asg-1")
        m2 = self._make_machine("i-bbb", "aws-prod", None, "asg-1")
        m3 = self._make_machine("i-ccc", "aws-prod", "EC2Fleet", "fleet-1")

        self._setup_uow({"i-aaa": m1, "i-bbb": m2, "i-ccc": m3})

        result = self.svc.group_by_resource(["i-aaa", "i-bbb", "i-ccc"])

        all_machines = [m for machines in result.values() for m in machines]
        machine_ids = {m.machine_id.value for m in all_machines}
        assert "i-aaa" in machine_ids
        assert "i-ccc" in machine_ids
        assert "i-bbb" not in machine_ids


# ---------------------------------------------------------------------------
# TemplateDefaultsService: raises ValueError instead of returning 'EC2Fleet'
# ---------------------------------------------------------------------------


class TestTemplateDefaultsServiceRaisesOnMissingProviderApi:
    def setup_method(self):
        from orb.application.services.template_defaults_service import TemplateDefaultsService

        self.logger = MagicMock()
        self.config_manager = MagicMock()
        # Make all config lookups return empty so we reach the final fallback
        self.config_manager.get_template_config.return_value = {}
        self.config_manager.get_provider_config.return_value = MagicMock(
            providers=[], provider_defaults={}
        )
        self.svc = TemplateDefaultsService(config_manager=self.config_manager, logger=self.logger)

    def test_raises_value_error_when_no_provider_api_configured(self):
        with pytest.raises(ValueError, match="provider_api"):
            self.svc.resolve_provider_api_default({})

    def test_raises_value_error_without_provider_instance(self):
        with pytest.raises(ValueError):
            self.svc.resolve_provider_api_default({}, provider_instance_name=None)
