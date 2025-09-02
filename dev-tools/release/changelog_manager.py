#!/usr/bin/env python3
"""
Comprehensive changelog manager for Open Host Factory Plugin.
Handles generation, updates, backfills, deletions, and validation.
"""

import argparse
import logging
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def clean_emojis(text: str) -> str:
    """Remove emojis and emoji-like characters from text."""
    # Remove common emojis and symbols
    emoji_pattern = re.compile(
        "["
        "\U0001f600-\U0001f64f"  # emoticons
        "\U0001f300-\U0001f5ff"  # symbols & pictographs
        "\U0001f680-\U0001f6ff"  # transport & map symbols
        "\U0001f1e0-\U0001f1ff"  # flags (iOS)
        "\U00002702-\U000027b0"  # dingbats
        "\U000024c2-\U0001f251"
        "]+",
        flags=re.UNICODE,
    )
    return emoji_pattern.sub("", text).strip()


class ChangelogManager:
    """Manages changelog generation and updates using git-changelog."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.changelog_path = project_root / "CHANGELOG.md"
        self.template_path = project_root / ".changelog-template.md"

    def run_command(
        self, cmd: list[str], capture_output: bool = True
    ) -> subprocess.CompletedProcess:
        """Run command with error handling."""
        try:
            result = subprocess.run(
                cmd, cwd=self.project_root, capture_output=capture_output, text=True, check=True
            )
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed: {' '.join(cmd)}")
            logger.error(f"Error: {e.stderr if e.stderr else str(e)}")
            raise

    def get_git_tags(self) -> list[str]:
        """Get all git tags sorted by version."""
        result = self.run_command(["git", "tag", "-l", "v*", "--sort=-version:refname"])
        return [tag.strip() for tag in result.stdout.split("\n") if tag.strip()]

    def get_commit_range(self, from_tag: Optional[str] = None, to_commit: str = "HEAD") -> str:
        """Get commit range for changelog generation."""
        if from_tag:
            return f"{from_tag}..{to_commit}"

        # Find last tag
        tags = self.get_git_tags()
        if tags:
            return f"{tags[0]}..{to_commit}"

        # First release - use all commits
        first_commit = self.run_command(
            ["git", "rev-list", "--max-parents=0", "HEAD"]
        ).stdout.strip()
        return f"{first_commit}..{to_commit}"

    def generate_full_changelog(self) -> None:
        """Generate complete changelog from git history."""
        logger.info("Generating full changelog from git history...")

        cmd = [
            "git-changelog",
            "--convention",
            "conventional",
            "--output",
            str(self.changelog_path),
        ]

        self.run_command(cmd, capture_output=False)
        logger.info(f"Changelog generated: {self.changelog_path}")

    def update_for_release(self, version: str, from_commit: Optional[str] = None) -> None:
        """Update changelog for a specific release."""
        logger.info(f"Updating changelog for release {version}")

        # Generate section for this version
        cmd = ["git-changelog", "--convention", "conventional", "--output", "-"]

        if from_commit:
            cmd.extend(["--filter-commits", f"{from_commit}..HEAD"])
        else:
            # Find last tag
            tags = self.get_git_tags()
            if tags:
                cmd.extend(["--filter-commits", f"{tags[0]}..HEAD"])

        result = self.run_command(cmd)
        version_section = result.stdout

        # Insert into existing changelog
        self._insert_version_section(version_section)

    def _insert_version_section(self, version_section: str) -> None:
        """Insert version section into changelog."""
        if not self.changelog_path.exists():
            # Create new changelog with this section
            with open(self.changelog_path, "w") as f:
                f.write(version_section)
            return

        # Read existing changelog
        with open(self.changelog_path) as f:
            content = f.read()

        # Insert after [Unreleased] section
        unreleased_pattern = r"(## \[Unreleased\].*?)(\n## \[|\Z)"

        def replace_func(match):
            """Replace unreleased section with version section."""
            return f"{match.group(1)}\n\n{version_section}\n{match.group(2)}"

        updated_content = re.sub(unreleased_pattern, replace_func, content, flags=re.DOTALL)

        # Write updated changelog
        with open(self.changelog_path, "w") as f:
            f.write(updated_content)

    def _insert_version_changelog(self, temp_changelog: Path, version: str) -> None:
        """Insert version changelog into main changelog."""
        if not self.changelog_path.exists():
            # First changelog - just copy
            temp_changelog.rename(self.changelog_path)
            return

        # Read temp changelog content (skip header)
        with open(temp_changelog) as f:
            temp_content = f.read()

        # Extract version section
        version_section = self._extract_version_section(temp_content, version)

        # Read existing changelog
        with open(self.changelog_path) as f:
            existing_content = f.read()

        # Insert new version after [Unreleased]
        unreleased_pattern = r"(## \[Unreleased\].*?)(\n## \[|\nZ)"

        def replace_func(match):
            """Insert version section after unreleased section."""
            return f"{match.group(1)}\n\n{version_section}{match.group(2)}"

        updated_content = re.sub(
            unreleased_pattern, replace_func, existing_content, flags=re.DOTALL
        )

        # Write updated changelog
        with open(self.changelog_path, "w") as f:
            f.write(updated_content)

    def _extract_version_section(self, content: str, version: str) -> str:
        """Extract version section from generated changelog."""
        # Find version section
        version_pattern = rf"## \[{re.escape(version)}\].*?(?=\n## \[|\nZ)"
        match = re.search(version_pattern, content, re.DOTALL)

        if match:
            return match.group(0).strip()

        logger.warning(f"Could not extract version section for {version}")
        return f"## [{version}] - {datetime.now().strftime('%Y-%m-%d')}\n\n### Changed\n- Release {version}"

    def delete_version(self, version: str) -> None:
        """Remove version from changelog."""
        logger.info(f"Removing version {version} from changelog")

        if not self.changelog_path.exists():
            logger.warning("Changelog does not exist")
            return

        with open(self.changelog_path) as f:
            content = f.read()

        # Remove version section
        version_pattern = rf"## \[{re.escape(version)}\].*?(?=\n## \[|\nZ)"
        updated_content = re.sub(version_pattern, "", content, flags=re.DOTALL)

        # Clean up extra newlines
        updated_content = re.sub(r"\n{3,}", "\n\n", updated_content)

        with open(self.changelog_path, "w") as f:
            f.write(updated_content)

        logger.info(f"Removed version {version} from changelog")

    def validate_changelog(self) -> bool:
        """Validate changelog format and content."""
        logger.info("Validating changelog...")

        if not self.changelog_path.exists():
            logger.error("Changelog does not exist")
            return False

        with open(self.changelog_path) as f:
            content = f.read()

        # Check format (git-changelog format)
        checks = [
            (r"# Changelog", "Missing main heading"),
            (r"## Unreleased", "Missing Unreleased section"),
            (r"keepachangelog\.com", "Missing Keep a Changelog reference"),
            (r"semver\.org", "Missing Semantic Versioning reference"),
        ]

        valid = True
        for pattern, error_msg in checks:
            if not re.search(pattern, content):
                logger.error(error_msg)
                valid = False

        # Check for duplicate versions
        versions = re.findall(r"## ([^\n]+)", content)
        if len(versions) != len(set(versions)):
            logger.error("Duplicate versions found")
            valid = False

        if valid:
            logger.info("Changelog validation passed")

        return valid

    def preview_changes(self, from_commit: Optional[str] = None) -> None:
        """Preview changelog changes without writing to file."""
        logger.info("Previewing changelog changes...")

        cmd = ["git-changelog", "--convention", "conventional", "--output", "-"]

        if from_commit:
            cmd.extend(["--filter-commits", f"{from_commit}..HEAD"])

        self.run_command(cmd, capture_output=False)

    def backfill_release(self, version: str, from_commit: str, to_commit: str) -> None:
        """Handle backfill release changelog update."""
        logger.info(f"Handling backfill release {version} ({from_commit}..{to_commit})")

        # Generate changelog for specific commit range
        cmd = [
            "git-changelog",
            "--convention",
            "conventional",
            "--filter-commits",
            f"{from_commit}..{to_commit}",
            "--output",
            "-",
        ]

        result = self.run_command(cmd)
        backfill_section = result.stdout

        # Insert backfill section into changelog
        self._insert_backfill_section(backfill_section, version)

    def _insert_backfill_section(self, backfill_section: str, version: str) -> None:
        """Insert backfill section into changelog in chronological order."""
        if not self.changelog_path.exists():
            # Create new changelog with this section
            with open(self.changelog_path, "w") as f:
                f.write(backfill_section)
            return

        # Read existing changelog
        with open(self.changelog_path) as f:
            content = f.read()

        # Extract version section from backfill output
        version_match = re.search(r"## Unreleased.*?(?=## |$)", backfill_section, re.DOTALL)
        if version_match:
            # Replace "Unreleased" with actual version
            version_section = version_match.group(0).replace(
                "## Unreleased",
                f"## [{version.lstrip('v')}] - {datetime.now().strftime('%Y-%m-%d')}",
            )

            # Insert after [Unreleased] section
            unreleased_pattern = r"(## Unreleased.*?)(\n## \[|\Z)"

            def replace_func(match):
                """Insert backfill version section after unreleased section."""
                return f"{match.group(1)}\n\n{version_section}\n{match.group(2)}"

            updated_content = re.sub(unreleased_pattern, replace_func, content, flags=re.DOTALL)

            # Write updated changelog
            with open(self.changelog_path, "w") as f:
                f.write(updated_content)

    def _insert_backfill_changelog(self, temp_changelog: Path, version: str) -> None:
        """Insert backfill changelog in chronological order."""
        # For backfills, regenerate entire changelog to maintain chronological order
        logger.info("Regenerating full changelog to maintain chronological order")
        self.generate_full_changelog()


def main():
    """Main entry point for changelog manager CLI."""
    parser = argparse.ArgumentParser(description="Changelog Manager")
    parser.add_argument(
        "--project-root", type=Path, default=Path.cwd(), help="Project root directory"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Generate command
    subparsers.add_parser("generate", help="Generate full changelog from git history")

    # Update command
    update_parser = subparsers.add_parser("update", help="Update changelog for release")
    update_parser.add_argument("version", help="Version to update")
    update_parser.add_argument("--from-commit", help="Starting commit for changes")

    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete version from changelog")
    delete_parser.add_argument("version", help="Version to delete")

    # Validate command
    subparsers.add_parser("validate", help="Validate changelog format")

    # Preview command
    preview_parser = subparsers.add_parser("preview", help="Preview changelog changes")
    preview_parser.add_argument("--from-commit", help="Starting commit for preview")

    # Backfill command
    backfill_parser = subparsers.add_parser("backfill", help="Handle backfill release")
    backfill_parser.add_argument("version", help="Version for backfill")
    backfill_parser.add_argument("from_commit", help="Starting commit")
    backfill_parser.add_argument("to_commit", help="Ending commit")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    manager = ChangelogManager(args.project_root)

    try:
        if args.command == "generate":
            manager.generate_full_changelog()
        elif args.command == "update":
            manager.update_for_release(args.version, args.from_commit)
        elif args.command == "delete":
            manager.delete_version(args.version)
        elif args.command == "validate":
            if not manager.validate_changelog():
                sys.exit(1)
        elif args.command == "preview":
            manager.preview_changes(args.from_commit)
        elif args.command == "backfill":
            manager.backfill_release(args.version, args.from_commit, args.to_commit)
    except Exception as e:
        logger.error(f"Command failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
