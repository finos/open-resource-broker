#!/usr/bin/env python3
"""
Development Tools Installation Script

Installs all development prerequisites for the Open Host Factory Plugin project.

Detects the operating system and installs tools using the appropriate package manager:
- macOS: Homebrew
- Windows: Chocolatey
- Ubuntu/Debian: apt
- RHEL/CentOS/Fedora: yum/dnf
- Generic Linux: curl/wget downloads

Tools installed:
- yq (YAML processor)
- hadolint (Dockerfile linter)
- trivy (Container security scanner)
- syft (SBOM generator)
- docker (Container runtime)
- uv (Python package manager)
- semgrep (Static analysis)
- trufflehog (Secrets scanner)
- pip-audit (Python package vulnerability scanner)

Usage:
    python dev-tools/scripts/install_dev_tools.py [--dry-run] [--tool TOOL]

Options:
    --dry-run    Show what would be installed without installing
    --tool TOOL  Install only specific tool
"""

import argparse
import logging
import platform
import subprocess
import sys

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class DevToolsInstaller:
    """Installs development tools based on the operating system."""

    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.os_type = platform.system().lower()
        self.distro = self._detect_os_distro()

        # Tool definitions with installation methods per OS
        self.tools = {
            "yq": {
                "description": "YAML processor (required for Makefile)",
                "check_cmd": ["yq", "--version"],
                "install": {
                    "darwin": ["brew", "install", "yq"],
                    "windows": ["choco", "install", "-y", "yq"],
                    "ubuntu": ["sudo", "apt", "install", "-y", "yq"],
                    "debian": ["sudo", "apt", "install", "-y", "yq"],
                    "rhel": ["sudo", "yum", "install", "-y", "yq"],
                    "centos": ["sudo", "yum", "install", "-y", "yq"],
                    "fedora": ["sudo", "dnf", "install", "-y", "yq"],
                    "generic": self._install_yq_generic,
                },
            },
            "hadolint": {
                "description": "Dockerfile linter",
                "check_cmd": ["hadolint", "--version"],
                "install": {
                    "darwin": ["brew", "install", "hadolint"],
                    "windows": ["choco", "install", "-y", "hadolint"],
                    "ubuntu": self._install_hadolint_generic,
                    "debian": self._install_hadolint_generic,
                    "rhel": self._install_hadolint_generic,
                    "centos": self._install_hadolint_generic,
                    "fedora": self._install_hadolint_generic,
                    "generic": self._install_hadolint_generic,
                },
            },
            "trivy": {
                "description": "Container security scanner",
                "check_cmd": ["trivy", "--version"],
                "install": {
                    "darwin": ["brew", "install", "trivy"],
                    "windows": ["choco", "install", "-y", "trivy"],
                    "ubuntu": self._install_trivy_generic,
                    "debian": self._install_trivy_generic,
                    "rhel": self._install_trivy_generic,
                    "centos": self._install_trivy_generic,
                    "fedora": self._install_trivy_generic,
                    "generic": self._install_trivy_generic,
                },
            },
            "syft": {
                "description": "SBOM generator",
                "check_cmd": ["syft", "version"],
                "install": {
                    "darwin": ["brew", "install", "syft"],
                    "windows": ["choco", "install", "-y", "syft"],
                    "ubuntu": self._install_syft_generic,
                    "debian": self._install_syft_generic,
                    "rhel": self._install_syft_generic,
                    "centos": self._install_syft_generic,
                    "fedora": self._install_syft_generic,
                    "generic": self._install_syft_generic,
                },
            },
            "docker": {
                "description": "Container runtime",
                "check_cmd": ["docker", "--version"],
                "install": {
                    "darwin": ["brew", "install", "--cask", "docker"],
                    "windows": ["choco", "install", "-y", "docker-desktop"],
                    "ubuntu": self._install_docker_ubuntu,
                    "debian": self._install_docker_ubuntu,
                    "rhel": self._install_docker_rhel,
                    "centos": self._install_docker_rhel,
                    "fedora": ["sudo", "dnf", "install", "-y", "docker"],
                    "generic": self._show_docker_manual_install,
                },
            },
            "uv": {
                "description": "Python package manager",
                "check_cmd": ["uv", "--version"],
                "install": {
                    "darwin": ["brew", "install", "uv"],
                    "windows": ["choco", "install", "-y", "uv"],
                    "ubuntu": self._install_uv_generic,
                    "debian": self._install_uv_generic,
                    "rhel": self._install_uv_generic,
                    "centos": self._install_uv_generic,
                    "fedora": self._install_uv_generic,
                    "generic": self._install_uv_generic,
                },
            },
            "semgrep": {
                "description": "Static analysis tool (optional)",
                "check_cmd": ["semgrep", "--version"],
                "install": {
                    "darwin": ["pip", "install", "semgrep"],
                    "windows": ["pip", "install", "semgrep"],
                    "ubuntu": ["pip", "install", "semgrep"],
                    "debian": ["pip", "install", "semgrep"],
                    "rhel": ["pip", "install", "semgrep"],
                    "centos": ["pip", "install", "semgrep"],
                    "fedora": ["pip", "install", "semgrep"],
                    "generic": ["pip", "install", "semgrep"],
                },
            },
            "actionlint": {
                "description": "GitHub Actions workflow linter",
                "check_cmd": ["actionlint", "-version"],
                "install": {
                    "darwin": ["brew", "install", "actionlint"],
                    "windows": ["choco", "install", "-y", "actionlint"],
                    "ubuntu": self._install_actionlint_generic,
                    "debian": self._install_actionlint_generic,
                    "rhel": self._install_actionlint_generic,
                    "centos": self._install_actionlint_generic,
                    "fedora": self._install_actionlint_generic,
                    "generic": self._install_actionlint_generic,
                },
            },
            "shellcheck": {
                "description": "Shell script linter",
                "check_cmd": ["shellcheck", "--version"],
                "install": {
                    "darwin": ["brew", "install", "shellcheck"],
                    "windows": ["choco", "install", "-y", "shellcheck"],
                    "ubuntu": ["sudo", "apt", "install", "-y", "shellcheck"],
                    "debian": ["sudo", "apt", "install", "-y", "shellcheck"],
                    "rhel": ["sudo", "yum", "install", "-y", "ShellCheck"],
                    "centos": ["sudo", "yum", "install", "-y", "ShellCheck"],
                    "fedora": ["sudo", "dnf", "install", "-y", "ShellCheck"],
                    "generic": self._install_shellcheck_generic,
                },
            },
            "trufflehog": {
                "description": "Secrets scanner (optional)",
                "check_cmd": ["trufflehog", "--version"],
                "install": {
                    "darwin": ["brew", "install", "trufflehog"],
                    "windows": ["choco", "install", "-y", "trufflehog"],
                    "ubuntu": self._install_trufflehog_generic,
                    "debian": self._install_trufflehog_generic,
                    "rhel": self._install_trufflehog_generic,
                    "centos": self._install_trufflehog_generic,
                    "fedora": self._install_trufflehog_generic,
                    "generic": self._install_trufflehog_generic,
                },
            },
            "pip-audit": {
                "description": "Python package vulnerability scanner",
                "check_cmd": ["pip-audit", "--version"],
                "install": {
                    "darwin": self._install_pip_audit_python,
                    "windows": self._install_pip_audit_python,
                    "ubuntu": self._install_pip_audit_python,
                    "debian": self._install_pip_audit_python,
                    "rhel": self._install_pip_audit_python,
                    "centos": self._install_pip_audit_python,
                    "fedora": self._install_pip_audit_python,
                    "generic": self._install_pip_audit_python,
                },
            },
        }

    def _detect_os_distro(self):
        """Detect operating system and distribution."""
        if self.os_type == "windows":
            return "windows"
        elif self.os_type == "darwin":
            return "darwin"
        elif self.os_type != "linux":
            return self.os_type

        try:
            with open("/etc/os-release", "r") as f:
                content = f.read().lower()
                if "ubuntu" in content:
                    return "ubuntu"
                elif "debian" in content:
                    return "debian"
                elif "rhel" in content or "red hat" in content:
                    return "rhel"
                elif "centos" in content:
                    return "centos"
                elif "fedora" in content:
                    return "fedora"
        except FileNotFoundError:
            pass

        return "generic"

    def _ensure_chocolatey(self):
        """Ensure Chocolatey is installed on Windows."""
        if self.distro != "windows":
            return True

        # Check if chocolatey is already installed
        try:
            subprocess.run(["choco", "--version"], check=True, capture_output=True)
            logger.info("Chocolatey is already installed")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        logger.info("Installing Chocolatey package manager...")

        if self.dry_run:
            logger.info("[DRY RUN] Would install Chocolatey")
            return True

        # Install Chocolatey using PowerShell
        powershell_cmd = [
            "powershell",
            "-Command",
            "Set-ExecutionPolicy Bypass -Scope Process -Force; "
            "[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; "
            "iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))",
        ]

        try:
            result = subprocess.run(powershell_cmd, check=True, capture_output=True, text=True)
            logger.info("Chocolatey installed successfully")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install Chocolatey: {e}")
            logger.error("Please install Chocolatey manually: https://chocolatey.org/install")
            return False

    def _run_command(self, cmd, description="", shell=False):
        """Run a command with dry-run support."""
        if isinstance(cmd, list):
            cmd_str = " ".join(cmd)
        else:
            cmd_str = cmd

        if self.dry_run:
            logger.info(f"[DRY RUN] Would run: {cmd_str}")
            return True

        logger.info(f"Running: {cmd_str}")
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True, shell=shell)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to run {cmd_str}: {e}")
            if e.stderr:
                logger.error(f"Error output: {e.stderr}")
            return False
        except FileNotFoundError:
            logger.error(f"Command not found: {cmd[0] if isinstance(cmd, list) else cmd}")
            return False

    def _install_yq_generic(self):
        """Install yq using curl."""
        arch = platform.machine().lower()
        if arch == "x86_64":
            arch = "amd64"
        elif arch in ["aarch64", "arm64"]:
            arch = "arm64"

        url = f"https://github.com/mikefarah/yq/releases/latest/download/yq_linux_{arch}"
        return self._run_command(
            ["sudo", "curl", "-L", url, "-o", "/usr/local/bin/yq"]
        ) and self._run_command(["sudo", "chmod", "+x", "/usr/local/bin/yq"])

    def _install_hadolint_generic(self):
        """Install hadolint using wget."""
        return self._run_command(
            [
                "sudo",
                "wget",
                "-O",
                "/usr/local/bin/hadolint",
                "https://github.com/hadolint/hadolint/releases/latest/download/hadolint-Linux-x86_64",
            ]
        ) and self._run_command(["sudo", "chmod", "+x", "/usr/local/bin/hadolint"])

    def _install_trivy_generic(self):
        """Install trivy using their install script."""
        return self._run_command(
            [
                "curl",
                "-sfL",
                "https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh",
                "|",
                "sh",
                "-s",
                "--",
                "-b",
                "/usr/local/bin",
            ]
        )

    def _install_syft_generic(self):
        """Install syft using their install script."""
        return self._run_command(
            [
                "curl",
                "-sSfL",
                "https://raw.githubusercontent.com/anchore/syft/main/install.sh",
                "|",
                "sh",
                "-s",
                "--",
                "-b",
                "/usr/local/bin",
            ]
        )

    def _install_uv_generic(self):
        """Install uv using their install script."""
        return self._run_command(["curl", "-LsSf", "https://astral.sh/uv/install.sh", "|", "sh"])

    def _install_actionlint_generic(self):
        """Install actionlint using official download script."""
        return self._run_command(
            "bash <(curl https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash)",
            shell=True,
        )

    def _install_shellcheck_generic(self):
        """Install shellcheck using binary download."""
        arch = platform.machine().lower()
        if arch == "x86_64":
            arch = "x86_64"
        elif arch in ["aarch64", "arm64"]:
            arch = "aarch64"

        url = f"https://github.com/koalaman/shellcheck/releases/latest/download/shellcheck-latest.linux.{arch}.tar.xz"
        return self._run_command(
            [
                "curl",
                "-L",
                url,
                "|",
                "sudo",
                "tar",
                "-xJ",
                "-C",
                "/usr/local/bin",
                "--strip-components=1",
                f"shellcheck-latest/shellcheck",
            ]
        )

    def _install_trufflehog_generic(self):
        """Install trufflehog using curl."""
        arch = platform.machine().lower()
        if arch == "x86_64":
            arch = "amd64"
        elif arch in ["aarch64", "arm64"]:
            arch = "arm64"

        url = f"https://github.com/trufflesecurity/trufflehog/releases/latest/download/trufflehog_linux_{arch}.tar.gz"
        return self._run_command(
            ["curl", "-L", url, "|", "sudo", "tar", "-xz", "-C", "/usr/local/bin", "trufflehog"]
        )

    def _install_pip_audit_python(self):
        """Install pip-audit using Python package manager."""
        # Try uv first, fall back to pip
        if self._run_command(["uv", "--version"], capture_output=True):
            return self._run_command(["uv", "tool", "install", "pip-audit"])
        else:
            return self._run_command(["pip", "install", "--user", "pip-audit"])

    def _install_docker_ubuntu(self):
        """Install Docker on Ubuntu/Debian."""
        commands = [
            ["sudo", "apt", "update"],
            ["sudo", "apt", "install", "-y", "ca-certificates", "curl", "gnupg"],
            ["sudo", "install", "-m", "0755", "-d", "/etc/apt/keyrings"],
            [
                "curl",
                "-fsSL",
                "https://download.docker.com/linux/ubuntu/gpg",
                "|",
                "sudo",
                "gpg",
                "--dearmor",
                "-o",
                "/etc/apt/keyrings/docker.gpg",
            ],
            ["sudo", "chmod", "a+r", "/etc/apt/keyrings/docker.gpg"],
            ["sudo", "apt", "update"],
            ["sudo", "apt", "install", "-y", "docker-ce", "docker-ce-cli", "containerd.io"],
        ]

        for cmd in commands:
            if not self._run_command(cmd):
                return False
        return True

    def _install_docker_rhel(self):
        """Install Docker on RHEL/CentOS."""
        commands = [
            ["sudo", "yum", "install", "-y", "yum-utils"],
            [
                "sudo",
                "yum-config-manager",
                "--add-repo",
                "https://download.docker.com/linux/centos/docker-ce.repo",
            ],
            ["sudo", "yum", "install", "-y", "docker-ce", "docker-ce-cli", "containerd.io"],
        ]

        for cmd in commands:
            if not self._run_command(cmd):
                return False
        return True

    def _show_docker_manual_install(self):
        """Show manual Docker installation instructions."""
        logger.info("Docker installation varies by distribution.")
        logger.info("Please visit: https://docs.docker.com/engine/install/")
        return True

    def is_tool_installed(self, tool_name):
        """Check if a tool is already installed."""
        tool_info = self.tools.get(tool_name)
        if not tool_info:
            return False

        try:
            subprocess.run(tool_info["check_cmd"], check=True, capture_output=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def install_tool(self, tool_name):
        """Install a specific tool."""
        if tool_name not in self.tools:
            logger.error(f"Unknown tool: {tool_name}")
            return False

        tool_info = self.tools[tool_name]

        # Check if already installed
        if self.is_tool_installed(tool_name):
            logger.info(f"{tool_name} is already installed")
            return True

        # Ensure Chocolatey is available on Windows
        if self.distro == "windows":
            if not self._ensure_chocolatey():
                return False

        logger.info(f"Installing {tool_name}: {tool_info['description']}")

        # Get installation method for current OS
        install_method = tool_info["install"].get(self.distro) or tool_info["install"].get(
            "generic"
        )

        if not install_method:
            logger.error(f"No installation method for {tool_name} on {self.distro}")
            return False

        # Execute installation
        if callable(install_method):
            return install_method()
        else:
            return self._run_command(install_method)

    def install_all_tools(self, required_only=False):
        """Install all development tools."""
        logger.info(f"Installing development tools for {self.distro}")

        # Required tools (needed for basic functionality)
        required_tools = ["yq", "uv", "docker"]

        # Optional tools (for security/quality checks)
        optional_tools = [
            "hadolint",
            "trivy",
            "syft",
            "semgrep",
            "trufflehog",
            "actionlint",
            "shellcheck",
        ]

        tools_to_install = required_tools
        if not required_only:
            tools_to_install.extend(optional_tools)

        results = {}
        for tool in tools_to_install:
            results[tool] = self.install_tool(tool)

        # Summary
        logger.info("\n=== Installation Summary ===")
        for tool, success in results.items():
            status = "PASS" if success else "FAIL"
            logger.info(f"{status} {tool}")

        failed = [tool for tool, success in results.items() if not success]
        if failed:
            logger.warning(f"Failed to install: {', '.join(failed)}")
            return False
        else:
            logger.info("All tools installed successfully!")
            return True


def main():
    """Main installation function."""
    parser = argparse.ArgumentParser(description="Install development tools")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be installed")
    parser.add_argument("--tool", help="Install only specific tool")
    parser.add_argument("--required-only", action="store_true", help="Install only required tools")

    args = parser.parse_args()

    installer = DevToolsInstaller(dry_run=args.dry_run)

    if args.tool:
        success = installer.install_tool(args.tool)
    else:
        success = installer.install_all_tools(required_only=args.required_only)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
