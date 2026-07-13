"""Regression tests: install_dev_tools never uses shell=True with curl-piped scripts.

Covers:
- _run_command accepts only list args (signature-level guard)
- Generic installers that previously used shell=True now use two-step
  curl-to-tempfile + sh/bash-on-tempfile pattern (list args only)
- --dry-run path works for all patched methods without spawning processes
- _install_syft_generic, _install_bun_generic, _install_actionlint_generic
  call subprocess with list args, never with a shell=True string command
- All temp files are unlinked after install (os.unlink called in finally)
- docker install builds a valid arg list with no literal "|" element
- windows path uses download-then-execute, not inline iex eval
- dry-run never calls subprocess
"""

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure dev-tools/setup is importable
_DEV_TOOLS_PATH = str(Path(__file__).parents[2] / "dev-tools" / "setup")
if _DEV_TOOLS_PATH not in sys.path:
    sys.path.insert(0, _DEV_TOOLS_PATH)

from install_dev_tools import DevToolsInstaller  # noqa: E402


@pytest.mark.unit
class TestRunCommandSignature:
    """_run_command must only accept list args and never use shell=True."""

    def test_run_command_signature_has_no_shell_param(self):
        """The shell= parameter must not appear in _run_command's signature."""
        sig = inspect.signature(DevToolsInstaller._run_command)
        assert "shell" not in sig.parameters, (
            "_run_command must not expose a shell= parameter — "
            "callers could accidentally pass shell=True"
        )

    def test_run_command_accepts_list(self):
        installer = DevToolsInstaller(dry_run=True)
        # Should log and return True without spawning anything
        result = installer._run_command(["echo", "hello"])
        assert result is True

    def test_run_command_dry_run_does_not_call_subprocess(self):
        installer = DevToolsInstaller(dry_run=True)
        with patch("subprocess.run") as mock_run:
            installer._run_command(["curl", "-o", "/tmp/x", "https://example.com"])
            mock_run.assert_not_called()


@pytest.mark.unit
class TestInstallScriptsDryRun:
    """All three previously-shell=True methods must work under --dry-run."""

    def setup_method(self):
        self.installer = DevToolsInstaller(dry_run=True)

    def test_install_syft_generic_dry_run(self):
        with patch("subprocess.run") as mock_run:
            result = self.installer._install_syft_generic()
        assert result is True
        mock_run.assert_not_called()

    def test_install_bun_generic_dry_run(self):
        with patch("subprocess.run") as mock_run:
            result = self.installer._install_bun_generic()
        assert result is True
        mock_run.assert_not_called()

    def test_install_actionlint_generic_dry_run(self):
        with patch("subprocess.run") as mock_run:
            result = self.installer._install_actionlint_generic()
        assert result is True
        mock_run.assert_not_called()

    def test_install_uv_generic_dry_run(self):
        with patch("subprocess.run") as mock_run:
            result = self.installer._install_uv_generic()
        assert result is True
        mock_run.assert_not_called()

    def test_install_trivy_generic_dry_run(self):
        with patch("subprocess.run") as mock_run:
            result = self.installer._install_trivy_generic()
        assert result is True
        mock_run.assert_not_called()

    def test_install_shellcheck_generic_dry_run(self):
        with patch("subprocess.run") as mock_run:
            result = self.installer._install_shellcheck_generic()
        assert result is True
        mock_run.assert_not_called()

    def test_install_trufflehog_generic_dry_run(self):
        with patch("subprocess.run") as mock_run:
            result = self.installer._install_trufflehog_generic()
        assert result is True
        mock_run.assert_not_called()

    def test_install_act_generic_dry_run(self):
        with patch("subprocess.run") as mock_run:
            result = self.installer._install_act_generic()
        assert result is True
        mock_run.assert_not_called()

    def test_install_bun_windows_dry_run(self):
        with patch("subprocess.run") as mock_run:
            result = self.installer._install_bun_windows()
        assert result is True
        mock_run.assert_not_called()


@pytest.mark.unit
class TestInstallScriptsUseListArgs:
    """Installer methods must call subprocess with list args, never shell=True."""

    def setup_method(self):
        self.installer = DevToolsInstaller(dry_run=False)

    def _capture_subprocess_calls(self, method):
        """Run *method* with subprocess.run mocked; return list of calls."""
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append((cmd, kwargs))
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        # mkstemp returns (fd, path); we mock it to return a fake fd and path
        with patch("subprocess.run", side_effect=fake_run):
            with patch("tempfile.mkstemp") as mock_mkstemp:
                mock_mkstemp.return_value = (999, "/tmp/fake_install.sh")
                with patch("os.close"):
                    with patch("os.unlink"):
                        method()

        return captured

    def _assert_no_shell_true(self, calls):
        for cmd, kwargs in calls:
            assert kwargs.get("shell") is not True, (
                f"subprocess.run called with shell=True on cmd={cmd!r}. "
                "All install commands must use list args without shell=True."
            )
            assert isinstance(cmd, list), (
                f"subprocess.run called with a string command {cmd!r}. "
                "Commands must be lists to prevent shell injection."
            )

    def test_install_syft_generic_uses_list_args(self):
        calls = self._capture_subprocess_calls(self.installer._install_syft_generic)
        assert len(calls) >= 1
        self._assert_no_shell_true(calls)
        curl_call_cmds = [c for c, _ in calls if c and c[0] == "curl"]
        assert curl_call_cmds, "Expected at least one curl call"
        for cmd in curl_call_cmds:
            assert "|" not in cmd, f"Pipe character found in list args: {cmd}"

    def test_install_bun_generic_uses_list_args(self):
        calls = self._capture_subprocess_calls(self.installer._install_bun_generic)
        assert len(calls) >= 1
        self._assert_no_shell_true(calls)
        curl_cmds = [c for c, _ in calls if c and c[0] == "curl"]
        for cmd in curl_cmds:
            assert "|" not in cmd

    def test_install_actionlint_generic_uses_list_args(self):
        calls = self._capture_subprocess_calls(self.installer._install_actionlint_generic)
        assert len(calls) >= 1
        self._assert_no_shell_true(calls)
        curl_cmds = [c for c, _ in calls if c and c[0] == "curl"]
        for cmd in curl_cmds:
            assert "|" not in cmd

    def test_install_uv_generic_uses_list_args(self):
        calls = self._capture_subprocess_calls(self.installer._install_uv_generic)
        assert len(calls) >= 1
        self._assert_no_shell_true(calls)

    def test_install_trivy_generic_uses_list_args(self):
        calls = self._capture_subprocess_calls(self.installer._install_trivy_generic)
        assert len(calls) >= 1
        self._assert_no_shell_true(calls)

    def test_install_shellcheck_generic_uses_list_args(self):
        calls = self._capture_subprocess_calls(self.installer._install_shellcheck_generic)
        assert len(calls) >= 1
        self._assert_no_shell_true(calls)
        curl_cmds = [c for c, _ in calls if c and c[0] == "curl"]
        assert curl_cmds, "Expected curl call for shellcheck"
        for cmd in curl_cmds:
            assert "|" not in cmd

    def test_install_trufflehog_generic_uses_list_args(self):
        calls = self._capture_subprocess_calls(self.installer._install_trufflehog_generic)
        assert len(calls) >= 1
        self._assert_no_shell_true(calls)
        curl_cmds = [c for c, _ in calls if c and c[0] == "curl"]
        assert curl_cmds, "Expected curl call for trufflehog"
        for cmd in curl_cmds:
            assert "|" not in cmd

    def test_install_act_generic_uses_list_args(self):
        calls = self._capture_subprocess_calls(self.installer._install_act_generic)
        assert len(calls) >= 1
        self._assert_no_shell_true(calls)
        curl_cmds = [c for c, _ in calls if c and c[0] == "curl"]
        assert curl_cmds, "Expected curl call for act"
        for cmd in curl_cmds:
            assert "|" not in cmd

    def test_install_syft_two_step_pattern(self):
        """syft install must be curl-download then sh-execute (two subprocess calls)."""
        calls = self._capture_subprocess_calls(self.installer._install_syft_generic)
        cmds = [c for c, _ in calls]
        assert len(cmds) >= 2, "Expected two subprocess calls: curl download then sh execute"
        assert cmds[0][0] == "curl", f"First call should be curl, got {cmds[0][0]}"
        assert cmds[1][0] == "sh", f"Second call should be sh, got {cmds[1][0]}"

    def test_install_bun_two_step_pattern(self):
        """bun install must be curl-download then bash-execute."""
        calls = self._capture_subprocess_calls(self.installer._install_bun_generic)
        cmds = [c for c, _ in calls]
        assert len(cmds) >= 2
        assert cmds[0][0] == "curl"
        assert cmds[1][0] == "bash"

    def test_install_actionlint_two_step_pattern(self):
        """actionlint install must be curl-download then bash-execute."""
        calls = self._capture_subprocess_calls(self.installer._install_actionlint_generic)
        cmds = [c for c, _ in calls]
        assert len(cmds) >= 2
        assert cmds[0][0] == "curl"
        assert cmds[1][0] == "bash"

    def test_install_shellcheck_two_step_pattern(self):
        """shellcheck install must be curl-download then tar-extract (two subprocess calls)."""
        calls = self._capture_subprocess_calls(self.installer._install_shellcheck_generic)
        cmds = [c for c, _ in calls]
        assert len(cmds) >= 2
        assert cmds[0][0] == "curl"
        assert cmds[1][0] == "sudo"
        assert "tar" in cmds[1], f"Second call should invoke tar, got: {cmds[1]}"

    def test_install_trufflehog_two_step_pattern(self):
        """trufflehog install must be curl-download then tar-extract (two subprocess calls)."""
        calls = self._capture_subprocess_calls(self.installer._install_trufflehog_generic)
        cmds = [c for c, _ in calls]
        assert len(cmds) >= 2
        assert cmds[0][0] == "curl"
        assert cmds[1][0] == "sudo"
        assert "tar" in cmds[1], f"Second call should invoke tar, got: {cmds[1]}"

    def test_install_act_two_step_pattern(self):
        """act install must be curl-download then bash-execute (two subprocess calls)."""
        calls = self._capture_subprocess_calls(self.installer._install_act_generic)
        cmds = [c for c, _ in calls]
        assert len(cmds) >= 2
        assert cmds[0][0] == "curl"
        assert cmds[1][0] == "sudo"
        assert cmds[1][1] == "bash"


@pytest.mark.unit
class TestTempFileCleanup:
    """All temp-file-based installers must call os.unlink after install."""

    _TEMP_METHODS = [
        "_install_trivy_generic",
        "_install_syft_generic",
        "_install_uv_generic",
        "_install_bun_generic",
        "_install_actionlint_generic",
        "_install_shellcheck_generic",
        "_install_trufflehog_generic",
        "_install_act_generic",
    ]

    def _run_with_mocks(self, method_name):
        """Run installer method with subprocess + mkstemp fully mocked."""
        installer = DevToolsInstaller(dry_run=False)
        method = getattr(installer, method_name)

        fake_fd = 42
        fake_path = f"/tmp/fake_{method_name}.sh"

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with patch("subprocess.run", side_effect=fake_run):
            with patch("tempfile.mkstemp", return_value=(fake_fd, fake_path)):
                with patch("os.close") as mock_close:
                    with patch("os.unlink") as mock_unlink:
                        method()
                        return mock_close, mock_unlink, fake_fd, fake_path

    @pytest.mark.parametrize("method_name", _TEMP_METHODS)
    def test_temp_file_unlinked_on_success(self, method_name):
        """os.unlink must be called with the temp path after a successful install."""
        _, mock_unlink, _, fake_path = self._run_with_mocks(method_name)
        mock_unlink.assert_called_once_with(fake_path)

    @pytest.mark.parametrize("method_name", _TEMP_METHODS)
    def test_temp_file_unlinked_on_subprocess_failure(self, method_name):
        """os.unlink must be called even when subprocess.run raises CalledProcessError."""
        import subprocess as sp

        installer = DevToolsInstaller(dry_run=False)
        method = getattr(installer, method_name)

        fake_path = f"/tmp/fake_{method_name}_fail.sh"

        def fake_run_fail(cmd, **kwargs):
            raise sp.CalledProcessError(1, cmd)

        with patch("subprocess.run", side_effect=fake_run_fail):
            with patch("tempfile.mkstemp", return_value=(42, fake_path)):
                with patch("os.close"):
                    with patch("os.unlink") as mock_unlink:
                        method()
                        mock_unlink.assert_called_once_with(fake_path)

    @pytest.mark.parametrize("method_name", _TEMP_METHODS)
    def test_no_delete_false_in_source(self, method_name):
        """The method source must not contain NamedTemporaryFile(delete=False)."""
        installer = DevToolsInstaller(dry_run=False)
        method = getattr(installer, method_name)
        source = inspect.getsource(method)
        assert "delete=False" not in source, (
            f"{method_name} still uses NamedTemporaryFile(delete=False) "
            "which leaks temp files on failure"
        )


@pytest.mark.unit
class TestDockerInstallNoLiteralPipe:
    """_install_docker_ubuntu must not contain a literal '|' element in any command list."""

    def test_docker_ubuntu_no_pipe_element(self):
        """No command list passed to _run_command may contain '|' as an element."""
        installer = DevToolsInstaller(dry_run=False)
        all_commands = []

        def capture_run_command(cmd, description=""):
            all_commands.append(cmd)
            return True

        installer._run_command = capture_run_command
        installer._install_docker_ubuntu()

        for cmd in all_commands:
            assert "|" not in cmd, (
                f"Literal '|' found in command list {cmd!r}. "
                "subprocess with shell=False does not interpret pipes — "
                "use two separate subprocess calls or curl -o directly."
            )

    def test_docker_ubuntu_commands_are_lists(self):
        """Every command dispatched by _install_docker_ubuntu must be a list."""
        installer = DevToolsInstaller(dry_run=False)

        def capture_run_command(cmd, description=""):
            assert isinstance(cmd, list), f"Command must be a list, got {type(cmd)}: {cmd!r}"
            return True

        installer._run_command = capture_run_command
        result = installer._install_docker_ubuntu()
        assert result is True

    def test_docker_ubuntu_gpg_step_uses_curl_dash_o(self):
        """The GPG key step must use curl -o to write directly to the keyring path."""
        installer = DevToolsInstaller(dry_run=False)
        captured = []

        def capture_run_command(cmd, description=""):
            captured.append(cmd)
            return True

        installer._run_command = capture_run_command
        installer._install_docker_ubuntu()

        # Find the curl command that downloads the GPG key
        curl_cmds = [
            c for c in captured if c and c[0] in ("curl", "sudo") and "docker.com" in " ".join(c)
        ]
        assert curl_cmds, "Expected a curl command fetching the Docker GPG key"
        for cmd in curl_cmds:
            assert "|" not in cmd, f"Pipe element found in GPG key command: {cmd}"
            # Must write to a file path, not pipe to gpg
            assert "-o" in cmd or "--output" in cmd, (
                f"GPG key download must use -o to write to file, got: {cmd}"
            )


@pytest.mark.unit
class TestWindowsNoPipeToExec:
    """Windows installers must not use irm ... | iex or DownloadString + iex patterns."""

    def test_bun_windows_not_in_tools_as_iex_string(self):
        """The bun windows entry must not be the old irm|iex string command."""
        installer = DevToolsInstaller(dry_run=True)
        bun_windows = installer.tools["bun"]["install"]["windows"]
        if isinstance(bun_windows, list):
            cmd_str = " ".join(bun_windows)
            assert "iex" not in cmd_str.lower(), (
                f"bun windows installer still uses iex inline eval: {cmd_str!r}"
            )
        else:
            # It's a callable (_install_bun_windows) — that's the correct fix
            assert callable(bun_windows), "bun windows entry must be callable or a safe list"

    def test_install_bun_windows_method_exists(self):
        """_install_bun_windows must exist as a method (the fixed Windows path)."""
        installer = DevToolsInstaller(dry_run=True)
        assert hasattr(installer, "_install_bun_windows"), (
            "_install_bun_windows method must exist to handle Windows bun install "
            "without inline iex eval"
        )
        assert callable(installer._install_bun_windows)

    def test_ensure_chocolatey_source_has_no_iex_inline(self):
        """_ensure_chocolatey must not use DownloadString + iex inline eval."""
        installer = DevToolsInstaller(dry_run=True)
        source = inspect.getsource(installer._ensure_chocolatey)
        # The old pattern: iex ((New-Object...).DownloadString(...))
        assert "DownloadString" not in source or "Invoke-WebRequest" in source, (
            "_ensure_chocolatey still uses DownloadString + iex inline eval. "
            "Must use Invoke-WebRequest -OutFile then execute the saved file."
        )
        # iex may appear in comments but not as a PowerShell command
        # Strip comment lines and check
        non_comment_lines = [
            line for line in source.splitlines() if not line.strip().startswith("#")
        ]
        for line in non_comment_lines:
            assert "| iex" not in line and "iex (" not in line, (
                f"_ensure_chocolatey has inline iex eval on line: {line!r}"
            )

    def test_install_bun_windows_dry_run_no_subprocess(self):
        """_install_bun_windows dry-run must not call subprocess."""
        installer = DevToolsInstaller(dry_run=True)
        with patch("subprocess.run") as mock_run:
            result = installer._install_bun_windows()
        assert result is True
        mock_run.assert_not_called()


@pytest.mark.unit
class TestDryRunNoSubprocess:
    """--dry-run must prevent ALL subprocess calls across every installer method."""

    _ALL_GENERIC_METHODS = [
        "_install_trivy_generic",
        "_install_syft_generic",
        "_install_uv_generic",
        "_install_bun_generic",
        "_install_bun_windows",
        "_install_actionlint_generic",
        "_install_shellcheck_generic",
        "_install_trufflehog_generic",
        "_install_act_generic",
        "_install_yq_generic",
        "_install_hadolint_generic",
    ]

    @pytest.mark.parametrize("method_name", _ALL_GENERIC_METHODS)
    def test_dry_run_no_subprocess(self, method_name):
        installer = DevToolsInstaller(dry_run=True)
        method = getattr(installer, method_name)
        with patch("subprocess.run") as mock_run:
            result = method()
        assert result is True, f"{method_name} dry-run returned {result!r}, expected True"
        assert mock_run.call_count == 0, (
            f"{method_name} called subprocess.run under --dry-run: {mock_run.call_args_list}"
        )
