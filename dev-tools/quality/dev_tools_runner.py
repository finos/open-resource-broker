#!/usr/bin/env python3
"""
Development Tools Runner - Consolidated simple tool wrappers.

Consolidates: clean_whitespace.py, check_file_sizes.py, venv_setup.py, hadolint_check.py, deps_manager.py
"""

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

try:
    import pathspec
except ImportError:
    pathspec = None


def clean_whitespace():
    """Clean whitespace from Python files."""

    def load_gitignore_spec(root_dir: Path):
        if pathspec is None:
            return None
        gitignore_path = root_dir / ".gitignore"
        if gitignore_path.exists():
            with open(gitignore_path, encoding="utf-8") as f:
                return pathspec.PathSpec.from_lines("gitwildmatch", f)
        return pathspec.PathSpec.from_lines("gitwildmatch", [])

    def clean_file(file_path: Path) -> bool:
        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
            original_content = content
            lines = content.splitlines()
            cleaned_lines = [line.rstrip() for line in lines]
            if cleaned_lines and cleaned_lines[-1]:
                cleaned_lines.append("")
            cleaned_content = "\n".join(cleaned_lines)
            if cleaned_content != original_content:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(cleaned_content)
                return True
            return False
        except Exception as e:
            logger.error(f"Error processing {file_path}: {e}")
            return False

    root_dir = Path.cwd()
    gitignore_spec = load_gitignore_spec(root_dir)

    files_cleaned = 0
    for file_path in root_dir.rglob("*.py"):
        # Skip .venv and other common directories
        if any(
            part in [".venv", ".git", "__pycache__", "node_modules", ".pytest_cache"]
            for part in file_path.parts
        ):
            continue
        # Skip if gitignore available and file matches
        if gitignore_spec and gitignore_spec.match_file(str(file_path.relative_to(root_dir))):
            continue
        if clean_file(file_path):
            files_cleaned += 1
            logger.info(f"Cleaned: {file_path}")

    logger.info(f"Cleaned {files_cleaned} files")


def check_file_sizes(warn_only=False, threshold=600):
    """Check for files that are getting too large."""
    large_files = []

    src_dir = Path("src")
    if not src_dir.exists():
        logger.warning("src/ directory not found, skipping file size check")
        return

    for file_path in src_dir.rglob("*.py"):
        try:
            line_count = len(file_path.read_text(encoding="utf-8").splitlines())
            if line_count > threshold:
                large_files.append((file_path, line_count))
        except Exception as e:
            logger.warning(f"Could not read {file_path}: {e}")

    if large_files:
        logger.warning(f"Found {len(large_files)} files over {threshold} lines:")
        for file_path, line_count in sorted(large_files, key=lambda x: x[1], reverse=True):
            logger.warning(f"  {file_path}: {line_count} lines")

        if not warn_only:
            logger.error("Large files detected. Consider refactoring.")
            sys.exit(1)
    else:
        logger.info(f"All files are under {threshold} lines")


def venv_setup():
    """Setup virtual environment with uv or pip fallback."""
    venv_dir = Path(".venv")
    python_exe = sys.executable

    # Create venv if it doesn't exist
    if not venv_dir.exists():
        logger.info("Creating virtual environment...")
        subprocess.run([python_exe, "-m", "venv", str(venv_dir)], check=True)

    # Determine pip path
    if sys.platform == "win32":
        pip_path = venv_dir / "Scripts" / "pip"
    else:
        pip_path = venv_dir / "bin" / "pip"

    # Upgrade pip using uv or pip
    if shutil.which("uv"):
        logger.info("Using uv for virtual environment setup...")
        subprocess.run(["uv", "pip", "install", "--upgrade", "pip"], check=True)
    else:
        logger.info("Using pip for virtual environment setup...")
        subprocess.run([str(pip_path), "install", "--upgrade", "pip"], check=True)

    # Touch activate file
    if sys.platform == "win32":
        activate_file = venv_dir / "Scripts" / "activate"
    else:
        activate_file = venv_dir / "bin" / "activate"

    activate_file.touch()
    logger.info("Virtual environment setup complete!")


def hadolint_check(files=None, install_help=False):
    """Check Dockerfiles with hadolint."""
    if install_help:
        logger.info("Install hadolint:")
        logger.info("  macOS: brew install hadolint")
        logger.info("  Linux: See https://github.com/hadolint/hadolint#install")
        return

    if not shutil.which("hadolint"):
        logger.error("Error: hadolint not found")
        logger.info("Install with: brew install hadolint")
        return False

    # Default files to check
    files = files or ["Dockerfile", "dev-tools/docker/Dockerfile.dev-tools"]

    exit_code = 0
    for file_path in files:
        dockerfile = Path(file_path)
        if not dockerfile.exists():
            logger.warning(f"Warning: {dockerfile} not found, skipping")
            continue

        logger.info(f"Checking {dockerfile} with hadolint...")
        try:
            subprocess.run(["hadolint", str(dockerfile)], check=True)
        except subprocess.CalledProcessError:
            logger.info(f"Hadolint found issues in {dockerfile}")
            exit_code = 1

    if exit_code == 0:
        logger.info("All Dockerfiles passed hadolint checks!")
    return exit_code == 0


def deps_add(package, dev=False):
    """Add a dependency using uv."""
    if not package:
        logger.error("Error: Package name is required")
        return False

    cmd = ["uv", "add"]
    if dev:
        cmd.append("--dev")
    cmd.append(package)

    logger.info(f"Adding {'dev ' if dev else ''}dependency: {package}")
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to add dependency: {e}")
        return False
    except FileNotFoundError:
        logger.error("Command not found: uv")
        return False


def main():
    parser = argparse.ArgumentParser(description="Development tools runner")
    parser.add_argument(
        "command",
        choices=[
            "clean-whitespace",
            "check-file-sizes",
            "venv-setup",
            "hadolint-check",
            "deps-add",
        ],
    )
    parser.add_argument("--warn-only", action="store_true", help="Only warn, don't fail")
    parser.add_argument("--threshold", type=int, default=600, help="Line count threshold")
    parser.add_argument("--install-help", action="store_true", help="Show installation help")
    parser.add_argument("--dev", action="store_true", help="Add as dev dependency")
    parser.add_argument("files", nargs="*", help="Files to process")

    args = parser.parse_args()

    if args.command == "clean-whitespace":
        clean_whitespace()
    elif args.command == "check-file-sizes":
        check_file_sizes(warn_only=args.warn_only, threshold=args.threshold)
    elif args.command == "venv-setup":
        venv_setup()
    elif args.command == "hadolint-check":
        success = hadolint_check(files=args.files, install_help=args.install_help)
        if not success and not args.install_help:
            sys.exit(1)
    elif args.command == "deps-add":
        if not args.files:
            logger.error("Package name required for deps-add")
            sys.exit(1)
        success = deps_add(args.files[0], dev=args.dev)
        if not success:
            sys.exit(1)


if __name__ == "__main__":
    main()
