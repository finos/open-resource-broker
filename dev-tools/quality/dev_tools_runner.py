#!/usr/bin/env python3
"""
Development Tools Runner - Consolidated simple tool wrappers.

Consolidates: clean_whitespace.py, check_file_sizes.py, venv_setup.py, hadolint_check.py, deps_manager.py
"""

import argparse
import json
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


DEFAULT_BASELINE = Path(__file__).parent / "file_size_baseline.json"
BASELINE_CMD = "make file-sizes-baseline"


def _scan_large_files(threshold):
    """Return {posix path: line count} for src/**/*.py files over the threshold."""
    large = {}
    for file_path in Path("src").rglob("*.py"):
        try:
            line_count = len(file_path.read_text(encoding="utf-8").splitlines())
        except Exception as e:
            logger.warning(f"Could not read {file_path}: {e}")
            continue
        if line_count > threshold:
            large[file_path.as_posix()] = line_count
    return large


def _load_baseline(baseline_path):
    try:
        data = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
        files = data["files"]
        if not isinstance(files, dict) or not all(
            isinstance(v, int) and not isinstance(v, bool) for v in files.values()
        ):
            raise ValueError("'files' must map paths to integer line counts")
        return files
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.error(f"Cannot read file size baseline {baseline_path}: {e}")
        sys.exit(1)


def _write_baseline(baseline_path, files):
    payload = {"files": dict(sorted(files.items()))}
    Path(baseline_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def check_file_sizes(
    warn_only=False,
    threshold=600,
    baseline_path=None,
    report=False,
    write_baseline=False,
    allow_increase=False,
):
    """Check src/**/*.py file sizes against a per-file baseline.

    A file over the threshold must be listed in the baseline, and its recorded
    size is a ratchet: it may not grow, and the record must follow it down.
    """
    if not Path("src").exists():
        logger.warning("src/ directory not found, skipping file size check")
        return

    baseline_path = Path(baseline_path) if baseline_path else DEFAULT_BASELINE
    large = _scan_large_files(threshold)

    if report:
        logger.info(f"Found {len(large)} files over {threshold} lines:")
        for path, count in sorted(large.items(), key=lambda x: x[1], reverse=True):
            logger.info(f"  {path}: {count} lines")
        return

    if write_baseline:
        old = _load_baseline(baseline_path) if baseline_path.exists() else {}
        new = {}
        for path, count in large.items():
            if path not in old:
                if allow_increase:
                    new[path] = count
                else:
                    logger.warning(
                        f"Not adding new oversized file {path} ({count}); use --allow-increase"
                    )
            elif count > old[path]:
                if allow_increase:
                    new[path] = count
                else:
                    new[path] = old[path]
                    logger.warning(
                        f"Not raising {path} from {old[path]} to {count}; use --allow-increase"
                    )
            else:
                new[path] = count
        _write_baseline(baseline_path, new)
        logger.info(f"Wrote {len(new)} entries to {baseline_path}")
        return

    baseline = _load_baseline(baseline_path)
    problems = []
    for path, count in sorted(large.items()):
        if path not in baseline:
            problems.append(f"{path}: {count} lines, over {threshold} and not in the baseline")
        elif count > baseline[path]:
            problems.append(f"{path}: grew from {baseline[path]} to {count} lines")
        elif count < baseline[path]:
            problems.append(
                f"{path}: shrank from {baseline[path]} to {count} lines; lower the baseline"
            )
    for path, recorded in sorted(baseline.items()):
        if path not in large:
            problems.append(
                f"{path}: recorded at {recorded} lines but now at or under {threshold} "
                "or gone; remove the stale entry"
            )

    if not problems:
        logger.info(f"File sizes match the baseline ({len(large)} files over {threshold} lines)")
        return

    logger.warning("File size baseline violations:")
    for problem in problems:
        logger.warning(f"  {problem}")
    logger.warning(
        "The recorded size is a ratchet: a file may only get smaller, and the recorded "
        f"number must follow it down. Run `{BASELINE_CMD}` and commit "
        f"{baseline_path.name}. Growth needs the explicit flag --allow-increase "
        "(`make file-sizes-baseline-init`) and a reason in the PR."
    )
    if not warn_only:
        logger.error("File size baseline check failed.")
        sys.exit(1)


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
        return True

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
    parser.add_argument(
        "--baseline", help="File size baseline JSON (default: file_size_baseline.json)"
    )
    parser.add_argument("--report", action="store_true", help="List large files and exit 0")
    parser.add_argument(
        "--write-baseline", action="store_true", help="Lower or remove baseline entries"
    )
    parser.add_argument(
        "--allow-increase",
        action="store_true",
        help="With --write-baseline, also record new files and larger counts",
    )
    parser.add_argument("--threshold", type=int, default=600, help="Line count threshold")
    parser.add_argument("--install-help", action="store_true", help="Show installation help")
    parser.add_argument("--dev", action="store_true", help="Add as dev dependency")
    parser.add_argument("files", nargs="*", help="Files to process")

    args = parser.parse_args()

    if args.command == "clean-whitespace":
        clean_whitespace()
    elif args.command == "check-file-sizes":
        check_file_sizes(
            warn_only=args.warn_only,
            threshold=args.threshold,
            baseline_path=args.baseline,
            report=args.report,
            write_baseline=args.write_baseline,
            allow_increase=args.allow_increase,
        )
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
