"""
Tests for CLI argument parsing and flag propagation.

Calls parse_args() directly with sys.argv-style lists — no subprocess,
no DI container, no application initialization.
"""

import sys
from unittest.mock import patch

import pytest

from orb.cli.args import parse_args


def _parse(argv: list[str]):
    """Call parse_args with a controlled argv, return the Namespace."""
    with patch.object(sys, "argv", ["orb"] + argv):
        namespace, _ = parse_args()
    return namespace


class TestMachinesRequest:
    """machines request positional and flag arguments."""

    def test_positional_template_id_and_count(self):
        ns = _parse(["machines", "request", "template-id", "5"])
        assert ns.resource == "machines"
        assert ns.action == "request"
        assert ns.template_id == "template-id"
        assert ns.machine_count == 5

    def test_dry_run_flag(self):
        ns = _parse(["machines", "request", "template-id", "5", "--dry-run"])
        assert ns.dry_run is True

    def test_dry_run_defaults_false(self):
        ns = _parse(["machines", "request", "template-id", "5"])
        assert ns.dry_run is False

    def test_scheduler_override_default(self):
        ns = _parse(["machines", "request", "template-id", "5", "--scheduler", "default"])
        assert ns.scheduler == "default"

    def test_scheduler_override_hostfactory(self):
        ns = _parse(["machines", "request", "template-id", "5", "--scheduler", "hostfactory"])
        assert ns.scheduler == "hostfactory"

    def test_scheduler_invalid_choice_raises(self):
        with patch.object(
            sys, "argv", ["orb", "machines", "request", "t", "1", "--scheduler", "bad"]
        ):
            with pytest.raises(SystemExit) as exc_info:
                parse_args()
        assert exc_info.value.code == 2

    def test_flag_template_id(self):
        ns = _parse(["machines", "request", "--template-id", "my-tmpl", "--count", "3"])
        assert ns.flag_template_id == "my-tmpl"
        assert ns.flag_machine_count == 3

    def test_wait_flag(self):
        ns = _parse(["machines", "request", "template-id", "5", "--wait"])
        assert ns.wait is True

    def test_format_json(self):
        ns = _parse(["machines", "request", "template-id", "5", "--format", "json"])
        assert ns.format == "json"

    def test_format_defaults_to_json(self):
        ns = _parse(["machines", "request", "template-id", "5"])
        assert ns.format == "json"


class TestMachinesReturn:
    """machines return positional machine IDs."""

    def test_single_machine_id(self):
        ns = _parse(["machines", "return", "id1"])
        assert ns.resource == "machines"
        assert ns.action == "return"
        assert ns.machine_ids == ["id1"]

    def test_multiple_machine_ids(self):
        ns = _parse(["machines", "return", "id1", "id2"])
        assert ns.machine_ids == ["id1", "id2"]

    def test_no_machine_ids_gives_empty_list(self):
        ns = _parse(["machines", "return"])
        assert ns.machine_ids == []

    def test_force_flag(self):
        ns = _parse(["machines", "return", "id1", "--force"])
        assert ns.force is True

    def test_request_id_long_flag(self):
        ns = _parse(["machines", "return", "--request-id", "req-abc"])
        assert ns.request_id == "req-abc"

    def test_request_id_short_flag(self):
        ns = _parse(["machines", "return", "-r", "req-xyz"])
        assert ns.request_id == "req-xyz"

    def test_request_id_defaults_to_none(self):
        ns = _parse(["machines", "return", "id1"])
        assert ns.request_id is None


class TestFilterFlag:
    """--filter flag accumulates into a list."""

    def test_single_filter(self):
        ns = _parse(["machines", "list", "--filter", "status=running"])
        assert ns.filter == ["status=running"]

    def test_multiple_filters_accumulate(self):
        ns = _parse(
            ["machines", "list", "--filter", "status=running", "--filter", "template_id=t1"]
        )
        assert ns.filter == ["status=running", "template_id=t1"]

    def test_no_filter_is_none(self):
        ns = _parse(["machines", "list"])
        assert ns.filter is None

    def test_filter_with_tilde_operator(self):
        ns = _parse(["machines", "list", "--filter", "machine_types~t3"])
        assert ns.filter == ["machine_types~t3"]


class TestFormatFlag:
    """--format flag on various subcommands."""

    def test_format_yaml(self):
        ns = _parse(["machines", "list", "--format", "yaml"])
        assert ns.format == "yaml"

    def test_format_table(self):
        ns = _parse(["machines", "list", "--format", "table"])
        assert ns.format == "table"

    def test_format_list(self):
        ns = _parse(["machines", "list", "--format", "list"])
        assert ns.format == "list"

    def test_format_invalid_raises(self):
        with patch.object(sys, "argv", ["orb", "machines", "list", "--format", "xml"]):
            with pytest.raises(SystemExit) as exc_info:
                parse_args()
        assert exc_info.value.code == 2


class TestSingularAliases:
    """Singular resource aliases resolve to the same actions as plural forms."""

    def test_machine_alias_request(self):
        ns = _parse(["machine", "request", "tmpl", "2"])
        assert ns.resource == "machine"
        assert ns.action == "request"
        assert ns.template_id == "tmpl"
        assert ns.machine_count == 2

    def test_machine_alias_return(self):
        ns = _parse(["machine", "return", "id1", "id2"])
        assert ns.resource == "machine"
        assert ns.action == "return"
        assert ns.machine_ids == ["id1", "id2"]

    def test_template_alias_list(self):
        ns = _parse(["template", "list"])
        assert ns.resource == "template"
        assert ns.action == "list"

    def test_request_alias_status(self):
        ns = _parse(["request", "status", "req-123"])
        assert ns.resource == "request"
        assert ns.action == "status"
        assert ns.request_ids == ["req-123"]

    def test_provider_alias_list(self):
        ns = _parse(["provider", "list"])
        assert ns.resource == "provider"
        assert ns.action == "list"

    def test_infra_alias_discover(self):
        ns = _parse(["infra", "discover"])
        assert ns.resource == "infra"
        assert ns.action == "discover"


class TestGlobalFlags:
    """Global flags available on all subcommands."""

    def test_verbose_flag(self):
        ns = _parse(["machines", "list", "--verbose"])
        assert ns.verbose is True

    def test_quiet_flag(self):
        ns = _parse(["machines", "list", "--quiet"])
        assert ns.quiet is True

    def test_no_color_flag(self):
        ns = _parse(["machines", "list", "--no-color"])
        assert ns.no_color is True

    def test_provider_name_flag(self):
        ns = _parse(["machines", "list", "--provider-name", "aws-prod"])
        assert ns.provider_name == "aws-prod"

    def test_provider_type_flag(self):
        ns = _parse(["machines", "list", "--provider-type", "k8s"])
        assert ns.provider_type == "k8s"

    def test_provider_name_and_type_are_independent(self):
        ns = _parse(["machines", "list", "--provider-name", "aws-prod", "--provider-type", "aws"])
        assert ns.provider_name == "aws-prod"
        assert ns.provider_type == "aws"

    def test_limit_flag(self):
        ns = _parse(["machines", "list", "--limit", "10"])
        assert ns.limit == 10

    def test_offset_defaults_to_zero(self):
        ns = _parse(["machines", "list"])
        assert ns.offset == 0

    def test_offset_flag(self):
        ns = _parse(["machines", "list", "--offset", "20"])
        assert ns.offset == 20

    def test_yes_flag(self):
        ns = _parse(["machines", "return", "id1", "--yes"])
        assert ns.yes is True


class TestAllScope:
    """--all is scoped to the sub-commands that consume it, not global.

    After the CLI schema refactor, --all no longer lives on add_global_arguments
    (it had three different meanings). It is declared explicitly on the
    per-command sub-parsers where it is meaningful.
    """

    def test_all_present_on_machines_return(self):
        ns = _parse(["machines", "return", "--all", "--force"])
        assert ns.all is True

    def test_all_present_on_machines_stop(self):
        ns = _parse(["machines", "stop", "--all", "--force"])
        assert ns.all is True

    def test_all_present_on_machines_start(self):
        ns = _parse(["machines", "start", "--all"])
        assert ns.all is True

    def test_all_present_on_machines_status(self):
        ns = _parse(["machines", "status", "--all"])
        assert ns.all is True

    def test_all_present_on_requests_status(self):
        ns = _parse(["requests", "status", "--all"])
        assert ns.all is True

    def test_all_present_on_templates_validate(self):
        ns = _parse(["templates", "validate", "--all"])
        assert ns.all is True

    def test_all_absent_on_machines_list(self):
        """machines list never used --all — it must no longer be accepted."""
        with patch.object(sys, "argv", ["orb", "machines", "list", "--all"]):
            with pytest.raises(SystemExit) as exc_info:
                parse_args()
        assert exc_info.value.code == 2

    def test_all_absent_on_config_show(self):
        """config show never used --all — it must no longer be accepted."""
        with patch.object(sys, "argv", ["orb", "config", "show", "--all"]):
            with pytest.raises(SystemExit) as exc_info:
                parse_args()
        assert exc_info.value.code == 2


class TestHostFactoryCompatFlags:
    """-f/-d resolve to hf_file/hf_data only on HostFactory-invoked commands."""

    def test_hf_file_on_machines_request(self):
        ns = _parse(["machines", "request", "-f", "/tmp/in.json"])
        assert ns.hf_file == "/tmp/in.json"

    def test_hf_data_on_requests_status(self):
        ns = _parse(["requests", "status", "-d", '{"x":1}'])
        assert ns.hf_data == '{"x":1}'

    def test_hf_flags_absent_on_config_show(self):
        """config show is not HF-invoked — -f must not be accepted there."""
        with patch.object(sys, "argv", ["orb", "config", "show", "-f", "/tmp/x"]):
            with pytest.raises(SystemExit) as exc_info:
                parse_args()
        assert exc_info.value.code == 2


class TestRequestsWatch:
    """requests watch positional and flag arguments."""

    def test_watch_with_request_id(self):
        ns = _parse(["requests", "watch", "req-123"])
        assert ns.resource == "requests"
        assert ns.action == "watch"
        assert ns.request_id == "req-123"

    def test_watch_without_request_id(self):
        ns = _parse(["requests", "watch"])
        assert ns.resource == "requests"
        assert ns.action == "watch"
        assert ns.request_id is None

    def test_watch_interval_flag(self):
        ns = _parse(["requests", "watch", "req-123", "--interval", "2"])
        assert ns.interval == 2

    def test_watch_interval_default(self):
        ns = _parse(["requests", "watch"])
        assert ns.interval == 5
