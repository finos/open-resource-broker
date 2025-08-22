#!/usr/bin/env python3
"""
CQRS pattern validator for maintaining architectural consistency.

This script validates that CQRS handlers follow correct patterns and inheritance.
"""

import argparse
import ast
import logging
import sys
from pathlib import Path
from typing import List

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class CQRSValidator:
    """Validates CQRS pattern compliance."""

    def __init__(self):
        self.violations = []
        self.handler_files = []
        self.command_handlers = []
        self.query_handlers = []
        self.event_handlers = []

    def find_handler_files(self) -> List[Path]:
        """Find all handler files in the project."""
        handler_files = []

        # Look for handler files
        for pattern in ["*handler*.py", "*handlers.py"]:
            handler_files.extend(Path("src").rglob(pattern))

        # Also check application layer specifically
        app_path = Path("src/application")
        if app_path.exists():
            for file_path in app_path.rglob("*.py"):
                if "handler" in file_path.name.lower():
                    handler_files.append(file_path)

        return list(set(handler_files))  # Remove duplicates

    def analyze_handler_file(self, file_path: Path) -> None:
        """Analyze a single handler file for CQRS compliance."""
        try:
            content = file_path.read_text(encoding="utf-8")
            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    self.analyze_handler_class(node, file_path)

        except Exception as e:
            logger.warning(f"Could not analyze {file_path}: {e}")

    def analyze_handler_class(self, class_node: ast.ClassDef, file_path: Path) -> None:
        """Analyze a handler class for CQRS compliance."""
        class_name = class_node.name

        # Skip non-handler classes
        if "Handler" not in class_name:
            return

        # Get base class names
        base_names = []
        for base in class_node.bases:
            if hasattr(base, "id"):
                base_names.append(base.id)
            elif hasattr(base, "attr"):
                base_names.append(base.attr)

        # Check command handlers
        if "CommandHandler" in class_name:
            self.command_handlers.append((file_path, class_name))
            if not any("BaseCommandHandler" in base for base in base_names):
                self.violations.append(
                    f"{file_path}: {class_name} should inherit from BaseCommandHandler"
                )

        # Check query handlers
        elif "QueryHandler" in class_name:
            self.query_handlers.append((file_path, class_name))
            if not any("BaseQueryHandler" in base for base in base_names):
                self.violations.append(
                    f"{file_path}: {class_name} should inherit from BaseQueryHandler"
                )

        # Check event handlers
        elif "EventHandler" in class_name:
            self.event_handlers.append((file_path, class_name))
            if not any("BaseEventHandler" in base for base in base_names):
                self.violations.append(
                    f"{file_path}: {class_name} should inherit from BaseEventHandler"
                )

        # Check for required methods
        self.check_required_methods(class_node, class_name, file_path)

    def check_required_methods(
        self, class_node: ast.ClassDef, class_name: str, file_path: Path
    ) -> None:
        """Check if handler has required methods."""
        method_names = [node.name for node in class_node.body if isinstance(node, ast.FunctionDef)]

        if "CommandHandler" in class_name:
            required_methods = ["validate_command", "execute_command"]
            for method in required_methods:
                if method not in method_names:
                    self.violations.append(
                        f"{file_path}: {class_name} missing required method: {method}"
                    )

        elif "QueryHandler" in class_name:
            required_methods = ["validate_query", "execute_query"]
            for method in required_methods:
                if method not in method_names:
                    self.violations.append(
                        f"{file_path}: {class_name} missing required method: {method}"
                    )

    def validate_cqrs_handlers(self, warn_only: bool = False) -> None:
        """Main validation method."""
        self.handler_files = self.find_handler_files()

        if not self.handler_files:
            logger.info("No handler files found.")
            return

        logger.info(f"Analyzing {len(self.handler_files)} handler files...")

        for handler_file in self.handler_files:
            self.analyze_handler_file(handler_file)

        # Report findings
        self.report_findings(warn_only)

    def report_findings(self, warn_only: bool) -> None:
        """Report validation findings."""
        if self.violations:
            logger.warning("CQRS pattern violations detected:")
            logger.info("=" * 60)
            for violation in self.violations:
                logger.info(f"  {violation}")
            logger.info("=" * 60)
            logger.info("Consider updating handlers to follow CQRS patterns:")
            logger.info("- Command handlers should inherit from BaseCommandHandler")
            logger.info("- Query handlers should inherit from BaseQueryHandler")
            logger.info("- Event handlers should inherit from BaseEventHandler")
            logger.info("- Implement required validate_* and execute_* methods")

            if not warn_only:
                logger.error("Build failed due to CQRS violations.")
                sys.exit(1)
            else:
                logger.info("Build continues with warnings.")
        else:
            logger.info("All CQRS handlers follow correct patterns.")

        # Summary statistics
        logger.info("\nCQRS Handler Summary:")
        logger.info(f"  Command Handlers: {len(self.command_handlers)}")
        logger.info(f"  Query Handlers: {len(self.query_handlers)}")
        logger.info(f"  Event Handlers: {len(self.event_handlers)}")
        logger.info(f"  Total Handler Files: {len(self.handler_files)}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate CQRS pattern compliance in handler classes"
    )
    parser.add_argument("--warn-only", action="store_true", help="Only warn, don't fail the build")

    args = parser.parse_args()

    validator = CQRSValidator()
    validator.validate_cqrs_handlers(args.warn_only)


if __name__ == "__main__":
    main()
