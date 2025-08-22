#!/usr/bin/env python3
"""
Import validation script for pre-commit hooks.

This script validates that all critical imports work correctly and catches
issues that might be introduced during code refactoring or module reorganization.
"""

import importlib
import logging
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def validate_critical_imports():
    """Validate critical imports that are used by main entry points."""
    critical_imports = [
        # Bootstrap and main entry points
        "src.bootstrap",
        "src.run",
        # Domain core
        "src.domain.base.exceptions",
        "src.domain.request.value_objects",
        "src.domain.machine.value_objects",
        # Application layer
        "src.application.base.handlers",
        "src.application.commands.machine_handlers",
        "src.application.queries.handlers",
        # Infrastructure core
        "src.infrastructure.di.container",
        "src.infrastructure.logging.logger",
        "src.config.manager",
        # Interface layer
        "src.interface.command_handlers",
    ]

    failed_imports = []

    for module_name in critical_imports:
        try:
            importlib.import_module(module_name)
            logger.info(f"PASS {module_name}")
        except ImportError as e:
            failed_imports.append((module_name, str(e)))
            logger.error(f"FAIL {module_name}: {e}")
        except Exception as e:
            failed_imports.append((module_name, f"Unexpected error: {e}"))
            logger.warning(f"WARN {module_name}: Unexpected error: {e}")

    if failed_imports:
        logger.error(f"{len(failed_imports)} critical imports failed:")
        for module, error in failed_imports:
            logger.error(f"  - {module}: {error}")
        return False
    else:
        logger.info(f"All {len(critical_imports)} critical imports successful!")
        return True


def main():
    """Main entry point."""
    logger.info("Validating critical Python imports...")

    # Add project root to Python path so 'src' package can be imported
    # Path: dev-tools/scripts/validate_imports.py -> project-root
    project_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(project_root))

    success = validate_critical_imports()

    if not success:
        logger.info("Tip: Run the import validation tests for more detailed analysis:")
        logger.info("   python -m pytest tests/test_import_validation.py -v")
        sys.exit(1)

    logger.info("Import validation completed successfully!")


if __name__ == "__main__":
    main()
