#!/usr/bin/env python3
"""
Import validation script for pre-commit hooks.

This script validates that all critical imports work correctly and catches
issues that might be introduced during code refactoring or module reorganization.
"""
import sys
import importlib
from pathlib import Path


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
            print(f"PASS {module_name}")
        except ImportError as e:
            failed_imports.append((module_name, str(e)))
            print(f"FAIL {module_name}: {e}")
        except Exception as e:
            failed_imports.append((module_name, f"Unexpected error: {e}"))
            print(f"WARN {module_name}: Unexpected error: {e}")

    if failed_imports:
        print(f"\n{len(failed_imports)} critical imports failed:")
        for module, error in failed_imports:
            print(f"  - {module}: {error}")
        return False
    else:
        print(f"\n✅ All {len(critical_imports)} critical imports successful!")
        return True


def main():
    """Main entry point."""
    print("Validating critical Python imports...")

    # Add project root to Python path
    project_root = Path(__file__).parent.parent
    sys.path.insert(0, str(project_root))

    success = validate_critical_imports()

    if not success:
        print("\n💡 Tip: Run the import validation tests for more detailed analysis:")
        print("   python -m pytest tests/test_import_validation.py -v")
        sys.exit(1)

    print("\n🎉 Import validation completed successfully!")


if __name__ == "__main__":
    main()
