"""Unit tests for orb.cli.main — the CLI entry point.

These mock out the heavy collaborators (parse_args, DI container, the
Application bootstrap, command execution) so that each branch of main()'s
dispatch/error-handling logic can be exercised in isolation, without a real
AWS/DB/network environment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.cli import main as cli_main
from orb.domain.base.exceptions import DomainException


def _base_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        resource="system",
        action="status",
        completion=None,
        log_level="info",
        scheduler=None,
        verbose=False,
        config=None,
        dry_run=False,
        output=None,
        quiet=False,
        format="json",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _flush_telemetry
# ---------------------------------------------------------------------------


class TestFlushTelemetry:
    def test_calls_shutdown_telemetry(self, monkeypatch):
        shutdown = MagicMock()
        monkeypatch.setattr("orb.bootstrap.telemetry.shutdown_telemetry", shutdown)
        cli_main._flush_telemetry()
        shutdown.assert_called_once()

    def test_swallows_exception_from_shutdown(self, monkeypatch):
        monkeypatch.setattr(
            "orb.bootstrap.telemetry.shutdown_telemetry",
            MagicMock(side_effect=RuntimeError("telemetry boom")),
        )
        cli_main._flush_telemetry()  # must not raise

    def test_swallows_import_error(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "orb.bootstrap.telemetry":
                raise ImportError("no telemetry")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        cli_main._flush_telemetry()  # must not raise


# ---------------------------------------------------------------------------
# _show_resource_help
# ---------------------------------------------------------------------------


class TestShowResourceHelp:
    def test_invokes_subprocess_with_resource_help(self, monkeypatch):
        run_mock = MagicMock()
        monkeypatch.setattr("subprocess.run", run_mock)
        result = _run(cli_main._show_resource_help("machines"))
        assert result == {"success": True, "message": "Showed help for machines"}
        called_args = run_mock.call_args[0][0]
        assert called_args[-2:] == ["machines", "--help"]


# ---------------------------------------------------------------------------
# main(): completion generation
# ---------------------------------------------------------------------------


class TestMainCompletion:
    @pytest.mark.parametrize("shell", ["bash", "zsh"])
    def test_prints_completion_script_and_returns(self, monkeypatch, capsys, shell):
        args = _base_args(completion=shell)
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        monkeypatch.setattr("orb.cli.completion.generate_bash_completion", lambda: "BASH_SCRIPT")
        monkeypatch.setattr("orb.cli.completion.generate_zsh_completion", lambda: "ZSH_SCRIPT")
        monkeypatch.setattr(sys, "argv", ["orb"])

        _run(cli_main.main())

        out = capsys.readouterr().out
        assert ("BASH_SCRIPT" in out) or ("ZSH_SCRIPT" in out)

    def test_unknown_completion_shell_returns_without_printing(self, monkeypatch, capsys):
        args = _base_args(completion="fish")
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        monkeypatch.setattr(sys, "argv", ["orb"])

        _run(cli_main.main())  # must return cleanly, no SystemExit

        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# main(): SystemExit(2) retry-as-help path
# ---------------------------------------------------------------------------


class TestMainArgParseErrorHelpRetry:
    def test_recognized_resource_shows_help_and_exits_zero(self, monkeypatch):
        calls = {"n": 0}

        def fake_parse_args():
            calls["n"] += 1
            if calls["n"] == 1:
                raise SystemExit(2)
            raise SystemExit(0)

        monkeypatch.setattr(cli_main, "parse_args", fake_parse_args)
        monkeypatch.setattr(sys, "argv", ["orb", "machines", "--bogus"])

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 0
        assert calls["n"] == 2

    def test_unrecognized_resource_reraises_systemexit(self, monkeypatch):
        monkeypatch.setattr(cli_main, "parse_args", MagicMock(side_effect=SystemExit(2)))
        monkeypatch.setattr(sys, "argv", ["orb", "not-a-real-resource"])

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 2

    def test_non_arg_parse_systemexit_reraised_immediately(self, monkeypatch):
        """A SystemExit with a code other than 2 (e.g. a clean --help exit at
        the top level) must bypass the resource-help retry entirely."""
        monkeypatch.setattr(cli_main, "parse_args", MagicMock(side_effect=SystemExit(0)))
        monkeypatch.setattr(sys, "argv", ["orb", "--help"])

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 0

    def test_help_retry_with_nonzero_exit_reraises_original(self, monkeypatch):
        calls = {"n": 0}

        def fake_parse_args():
            calls["n"] += 1
            if calls["n"] == 1:
                raise SystemExit(2)
            raise SystemExit(1)  # help parse itself failed differently

        monkeypatch.setattr(cli_main, "parse_args", fake_parse_args)
        monkeypatch.setattr(sys, "argv", ["orb", "requests", "--bogus"])

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        # original SystemExit(2) is re-raised since help_exit.code != 0
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# main(): early resource-help display (no action)
# ---------------------------------------------------------------------------


class TestMainEarlyHelpDisplay:
    def test_prints_resource_parser_help_and_exits_zero(self, monkeypatch):
        args = _base_args(resource="machines", action=None, completion=None)
        fake_parser = MagicMock()
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {"machines": fake_parser}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 0
        fake_parser.print_help.assert_called_once()

    def test_continues_normally_when_resource_parser_missing(self, monkeypatch):
        """If the mapped resource has no entry in resource_parsers, execution
        must fall through to normal command processing instead of helping."""
        args = _base_args(resource="templates", action=None, completion=None)
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))  # no "templates" key
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        app_instance = MagicMock()
        app_instance.initialize = AsyncMock(return_value=True)
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))
        monkeypatch.setattr(cli_main, "execute_command", AsyncMock(return_value=("ok", 0)))

        _run(cli_main.main())  # must not raise SystemExit(0) from the help branch


# ---------------------------------------------------------------------------
# main(): scheduler override
# ---------------------------------------------------------------------------


class TestMainSchedulerOverride:
    def test_override_applied_and_restored(self, monkeypatch):
        args = _base_args(
            resource="system",
            action="status",
            scheduler="hostfactory",
        )
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())

        config = MagicMock()
        container = MagicMock()
        container.get.return_value = config
        monkeypatch.setattr(cli_main, "get_container", lambda: container)

        app_instance = MagicMock()
        app_instance.initialize = AsyncMock(return_value=True)
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))
        monkeypatch.setattr(cli_main, "execute_command", AsyncMock(return_value=("ok", 0)))

        _run(cli_main.main())

        config.override_scheduler_strategy.assert_called_once_with("hostfactory")
        config.restore_scheduler_strategy.assert_called_once()

    def test_override_failure_is_logged_not_raised(self, monkeypatch):
        args = _base_args(resource="system", action="status", scheduler="hostfactory")
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        monkeypatch.setattr(
            cli_main, "get_container", MagicMock(side_effect=RuntimeError("di boom"))
        )

        app_instance = MagicMock()
        app_instance.initialize = AsyncMock(return_value=True)
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))
        monkeypatch.setattr(cli_main, "execute_command", AsyncMock(return_value=("ok", 0)))

        _run(cli_main.main())  # must not raise despite override failure

    def test_restore_failure_in_finally_is_logged_not_raised(self, monkeypatch):
        args = _base_args(resource="system", action="status", scheduler="hostfactory")
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())

        config = MagicMock()
        config.restore_scheduler_strategy.side_effect = RuntimeError("restore boom")
        container = MagicMock()
        container.get.return_value = config
        monkeypatch.setattr(cli_main, "get_container", lambda: container)

        app_instance = MagicMock()
        app_instance.initialize = AsyncMock(return_value=True)
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))
        monkeypatch.setattr(cli_main, "execute_command", AsyncMock(return_value=("ok", 0)))

        _run(cli_main.main())  # must not raise despite restore failure
        config.restore_scheduler_strategy.assert_called_once()


# ---------------------------------------------------------------------------
# main(): init command dispatch
# ---------------------------------------------------------------------------


class TestMainInitDispatch:
    def test_dispatches_to_handle_init_and_exits_with_its_result(self, monkeypatch):
        args = _base_args(resource="init", action=None)
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr(cli_main, "get_container", MagicMock)
        monkeypatch.setattr(
            "orb.interface.init_command_handler.handle_init", AsyncMock(return_value=0)
        )

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 0

    def test_init_nonzero_exit_code_propagates(self, monkeypatch):
        args = _base_args(resource="init", action=None)
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr(cli_main, "get_container", MagicMock)
        monkeypatch.setattr(
            "orb.interface.init_command_handler.handle_init", AsyncMock(return_value=1)
        )

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 1

    def test_reuses_existing_container_when_already_attached(self, monkeypatch):
        args = _base_args(resource="init", action=None)
        args._container = MagicMock()  # already attached, non-None
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        get_container_mock = MagicMock()
        monkeypatch.setattr(cli_main, "get_container", get_container_mock)
        monkeypatch.setattr(
            "orb.interface.init_command_handler.handle_init", AsyncMock(return_value=0)
        )

        with pytest.raises(SystemExit):
            _run(cli_main.main())
        get_container_mock.assert_not_called()


# ---------------------------------------------------------------------------
# main(): mcp validate dispatch
# ---------------------------------------------------------------------------


class TestMainMcpValidate:
    def test_mcp_validate_prints_formatted_output_and_exits(self, monkeypatch):
        args = _base_args(resource="mcp", action="validate", format="json")
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())

        response = MagicMock(data={"ok": True}, exit_code=0)
        monkeypatch.setattr(
            "orb.interface.mcp.catalog_server.handle_mcp_validate",
            AsyncMock(return_value=response),
        )
        monkeypatch.setattr(
            "orb.cli.formatters.format_output", lambda data, fmt: f"formatted:{data}:{fmt}"
        )

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 0

    def test_mcp_validate_falls_back_to_json_for_unknown_format(self, monkeypatch, capsys):
        # "table" has no meaningful shape for the validation summary, so the
        # real formatter must render plain json rather than a table. A payload
        # containing a list is used so that a table rendering (if it were
        # mistakenly chosen) would be visibly distinct from the json output,
        # letting the assertion detect the fallback without depending on
        # mocking the formatter itself.
        data = {"items": [{"id": "abc"}]}
        args = _base_args(resource="mcp", action="validate", format="table")
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())

        response = MagicMock(data=data, exit_code=0)
        monkeypatch.setattr(
            "orb.interface.mcp.catalog_server.handle_mcp_validate",
            AsyncMock(return_value=response),
        )

        with pytest.raises(SystemExit):
            _run(cli_main.main())

        assert capsys.readouterr().out.strip() == json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# main(): k8s-legacy dispatch
# ---------------------------------------------------------------------------


class TestMainK8sLegacyDispatch:
    def test_delegates_and_exits_zero_if_handler_returns(self, monkeypatch):
        """handle_k8s_legacy always calls sys.exit() in real code; this test
        covers the defensive sys.exit(0) fallback in case it ever returns."""
        args = _base_args(resource="k8s-legacy", action=None)
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr(
            "orb.interface.cli.k8s_legacy.handle_k8s_legacy", MagicMock(return_value=None)
        )

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# main(): templates generate dispatch
# ---------------------------------------------------------------------------


class TestMainTemplatesGenerateDispatch:
    def test_success_exits_zero(self, monkeypatch):
        args = _base_args(resource="templates", action="generate")
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        monkeypatch.setattr(cli_main, "get_container", MagicMock)
        monkeypatch.setattr(
            "orb.interface.templates_generate_handler.handle_templates_generate",
            AsyncMock(return_value={"status": "success"}),
        )

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 0

    def test_failure_prints_error_and_exits_one(self, monkeypatch):
        args = _base_args(resource="templates", action="generate")
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        monkeypatch.setattr(cli_main, "get_container", MagicMock)
        monkeypatch.setattr(
            "orb.interface.templates_generate_handler.handle_templates_generate",
            AsyncMock(return_value={"status": "error", "message": "bad template"}),
        )

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 1

    def test_exception_prints_traceback_and_exits_one(self, monkeypatch):
        args = _base_args(resource="templates", action="generate")
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        monkeypatch.setattr(cli_main, "get_container", MagicMock)
        monkeypatch.setattr(
            "orb.interface.templates_generate_handler.handle_templates_generate",
            AsyncMock(side_effect=RuntimeError("template boom")),
        )

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 1

    def test_reuses_existing_container_when_already_attached(self, monkeypatch):
        args = _base_args(resource="templates", action="generate")
        args._container = MagicMock()
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        get_container_mock = MagicMock()
        monkeypatch.setattr(cli_main, "get_container", get_container_mock)
        monkeypatch.setattr(
            "orb.interface.templates_generate_handler.handle_templates_generate",
            AsyncMock(return_value={"status": "success"}),
        )

        with pytest.raises(SystemExit):
            _run(cli_main.main())
        get_container_mock.assert_not_called()


# ---------------------------------------------------------------------------
# main(): --log-level is applied to the root logger
# ---------------------------------------------------------------------------


class TestMainLogLevelApplied:
    @pytest.fixture(autouse=True)
    def _restore_root_log_level(self):
        """Root logger level is process-global state; restore it after each test."""
        original = logging.getLogger().level
        yield
        logging.getLogger().setLevel(original)

    def test_log_level_set_before_app_initialization(self, monkeypatch):
        """The flag must take effect even on early-exit paths (before bootstrap)."""
        args = _base_args(log_level="debug")
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        app_instance = MagicMock()
        app_instance.initialize = AsyncMock(return_value=True)
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))
        monkeypatch.setattr(cli_main, "execute_command", AsyncMock(return_value=("ok", 0)))

        _run(cli_main.main())

        assert logging.getLogger().level == logging.DEBUG

    def test_log_level_reapplied_after_bootstrap_overwrites_it(self, monkeypatch):
        """Application.initialize() re-runs setup_logging() from file config, which
        would reset the root logger level; --log-level must win regardless."""
        args = _base_args(log_level="debug")
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())

        async def _initialize_and_clobber_level(*_args, **_kwargs):
            # Simulate setup_logging() resetting the root logger to a
            # file-configured level different from the CLI flag.
            logging.getLogger().setLevel(logging.ERROR)
            return True

        app_instance = MagicMock()
        app_instance.initialize = _initialize_and_clobber_level
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))
        monkeypatch.setattr(cli_main, "execute_command", AsyncMock(return_value=("ok", 0)))

        _run(cli_main.main())

        assert logging.getLogger().level == logging.DEBUG

    def test_no_flag_keeps_level_from_config(self, monkeypatch):
        """Without --log-level, the level set by config/ORB_LOG_LEVEL is left alone."""
        args = _base_args(log_level=None)
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())

        async def _initialize_with_config_level(*_args, **_kwargs):
            # setup_logging() applies the config/env-configured level.
            logging.getLogger().setLevel(logging.WARNING)
            return True

        app_instance = MagicMock()
        app_instance.initialize = _initialize_with_config_level
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))
        monkeypatch.setattr(cli_main, "execute_command", AsyncMock(return_value=("ok", 0)))

        _run(cli_main.main())

        assert logging.getLogger().level == logging.WARNING

    def test_flag_overrides_level_from_config(self, monkeypatch):
        """An explicit --log-level wins over the level applied during bootstrap."""
        args = _base_args(log_level="ERROR")
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())

        async def _initialize_with_config_level(*_args, **_kwargs):
            logging.getLogger().setLevel(logging.DEBUG)
            return True

        app_instance = MagicMock()
        app_instance.initialize = _initialize_with_config_level
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))
        monkeypatch.setattr(cli_main, "execute_command", AsyncMock(return_value=("ok", 0)))

        _run(cli_main.main())

        assert logging.getLogger().level == logging.ERROR

    def test_flag_defaults_to_none(self):
        from orb.cli.args import parse_args

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sys, "argv", ["orb", "system", "status"])
            args, _ = parse_args()

        assert args.log_level is None


# ---------------------------------------------------------------------------
# main(): full application bootstrap + execute_command
# ---------------------------------------------------------------------------


class TestMainFullExecution:
    def _patch_common(self, monkeypatch, args, execute_command_result):
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        app_instance = MagicMock()
        app_instance.initialize = AsyncMock(return_value=True)
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))
        monkeypatch.setattr(
            cli_main, "execute_command", AsyncMock(return_value=execute_command_result)
        )
        return app_instance

    def test_application_initialize_failure_exits_one(self, monkeypatch):
        args = _base_args()
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        app_instance = MagicMock()
        app_instance.initialize = AsyncMock(return_value=False)
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 1

    def test_application_initialize_failure_prints_traceback_when_verbose(
        self, monkeypatch, capsys
    ):
        args = _base_args(verbose=True)
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        app_instance = MagicMock()
        app_instance.initialize = AsyncMock(return_value=False)
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 1
        assert "RuntimeError" in capsys.readouterr().err

    def test_successful_result_tuple_prints_output_and_exits_zero(self, monkeypatch, capsys):
        args = _base_args(output=None)
        self._patch_common(monkeypatch, args, ("formatted-output", 0))

        _run(cli_main.main())  # no SystemExit since exit_code == 0

        assert "formatted-output" in capsys.readouterr().out

    def test_result_string_without_tuple_defaults_exit_zero(self, monkeypatch, capsys):
        args = _base_args()
        self._patch_common(monkeypatch, args, "plain-output")

        _run(cli_main.main())

        assert "plain-output" in capsys.readouterr().out

    def test_nonzero_exit_code_from_result_propagates(self, monkeypatch, capsys):
        args = _base_args()
        self._patch_common(monkeypatch, args, ("bad-output", 7))

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 7

    def test_output_written_to_file(self, monkeypatch, tmp_path):
        out_file = tmp_path / "out.json"
        args = _base_args(output=str(out_file), quiet=True)
        self._patch_common(monkeypatch, args, ("file-output", 0))

        _run(cli_main.main())

        assert out_file.read_text() == "file-output"

    def test_output_written_to_file_prints_success_when_not_quiet(
        self, monkeypatch, tmp_path, capsys
    ):
        out_file = tmp_path / "out.json"
        args = _base_args(output=str(out_file), quiet=False)
        self._patch_common(monkeypatch, args, ("file-output", 0))

        _run(cli_main.main())

        assert "Output written to" in capsys.readouterr().out

    def test_dry_run_activates_dry_run_context(self, monkeypatch):
        args = _base_args(dry_run=True)
        execute_command_mock = AsyncMock(return_value=("dry-output", 0))
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        app_instance = MagicMock()
        app_instance.initialize = AsyncMock(return_value=True)
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))
        monkeypatch.setattr(cli_main, "execute_command", execute_command_mock)

        import importlib

        drc_module = importlib.import_module("orb.infrastructure.mocking.dry_run_context")

        dry_run_context_mock = MagicMock()
        dry_run_context_mock.return_value.__enter__ = MagicMock(return_value=None)
        dry_run_context_mock.return_value.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(drc_module, "dry_run_context", dry_run_context_mock)

        _run(cli_main.main())

        dry_run_context_mock.assert_called_once_with(True)
        execute_command_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# main(): error handling in execute_command
# ---------------------------------------------------------------------------


class TestMainExecuteCommandErrorHandling:
    def _patch_bootstrap(self, monkeypatch, args, execute_command_side_effect):
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        app_instance = MagicMock()
        app_instance.initialize = AsyncMock(return_value=True)
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))
        monkeypatch.setattr(
            cli_main, "execute_command", AsyncMock(side_effect=execute_command_side_effect)
        )

    def test_domain_exception_formatted_and_exit_code_used(self, monkeypatch, capsys):
        args = _base_args(quiet=False)
        self._patch_bootstrap(monkeypatch, args, DomainException("bad request", "ERR"))

        formatter = MagicMock()
        formatter.format_error.return_value = ("domain error output", 3)
        monkeypatch.setattr("orb.cli.response_formatter.create_cli_formatter", lambda: formatter)

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 3
        assert "domain error output" in capsys.readouterr().err

    def test_generic_exception_formatted_with_verbose_traceback(self, monkeypatch, capsys):
        args = _base_args(verbose=True, quiet=False)
        self._patch_bootstrap(monkeypatch, args, RuntimeError("unexpected boom"))

        formatter = MagicMock()
        formatter.format_error.return_value = ("generic error output", 1)
        monkeypatch.setattr("orb.cli.response_formatter.create_cli_formatter", lambda: formatter)

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "generic error output" in captured.err

    def test_quiet_suppresses_error_output(self, monkeypatch, capsys):
        args = _base_args(quiet=True)
        self._patch_bootstrap(monkeypatch, args, RuntimeError("silent boom"))

        formatter = MagicMock()
        formatter.format_error.return_value = ("should-not-print", 1)
        monkeypatch.setattr("orb.cli.response_formatter.create_cli_formatter", lambda: formatter)

        with pytest.raises(SystemExit):
            _run(cli_main.main())
        captured = capsys.readouterr()
        assert "should-not-print" not in captured.out
        assert "should-not-print" not in captured.err


# ---------------------------------------------------------------------------
# main(): top-level KeyboardInterrupt / fatal exception handling
# ---------------------------------------------------------------------------


class TestMainTopLevelHandlers:
    def test_keyboard_interrupt_exits_130(self, monkeypatch, capsys):
        monkeypatch.setattr(cli_main, "parse_args", MagicMock(side_effect=KeyboardInterrupt()))
        monkeypatch.setattr(sys, "argv", ["orb"])
        flush_mock = MagicMock()
        monkeypatch.setattr(cli_main, "_flush_telemetry", flush_mock)

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 130
        assert "cancelled" in capsys.readouterr().out.lower()
        # Called at least once from the KeyboardInterrupt branch (also in finally).
        assert flush_mock.call_count >= 1

    def test_unhandled_exception_prints_fatal_error_and_exits_one(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli_main, "parse_args", MagicMock(side_effect=ValueError("totally broken"))
        )
        monkeypatch.setattr(sys, "argv", ["orb"])

        with pytest.raises(SystemExit) as exc_info:
            _run(cli_main.main())
        assert exc_info.value.code == 1
        assert "Fatal error" in capsys.readouterr().err

    def test_flush_telemetry_always_called_in_finally(self, monkeypatch):
        args = _base_args()
        flush_mock = MagicMock()
        monkeypatch.setattr(cli_main, "_flush_telemetry", flush_mock)
        monkeypatch.setattr(cli_main, "parse_args", lambda: (args, {}))
        monkeypatch.setattr("orb.run.setup_environment", MagicMock())
        app_instance = MagicMock()
        app_instance.initialize = AsyncMock(return_value=True)
        monkeypatch.setattr("orb.bootstrap.Application", MagicMock(return_value=app_instance))
        monkeypatch.setattr(cli_main, "execute_command", AsyncMock(return_value=("ok", 0)))

        _run(cli_main.main())
        flush_mock.assert_called_once()
