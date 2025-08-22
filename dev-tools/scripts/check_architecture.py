#!/usr/bin/env python3
"""
Clean Architecture dependency rule validator.

This script validates that Clean Architecture dependency rules are followed:
- Domain layer should not depend on outer layers
- Application layer should not depend on Interface layer
- Dependencies should flow inward only
"""

import argparse
import ast
import logging
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class ArchitectureValidator:
    """Validates Clean Architecture dependency rules."""

    def __init__(self):
        self.violations = []
        self.layer_imports = {
            "domain": [],
            "application": [],
            "infrastructure": [],
            "interface": [],
        }

    def analyze_imports(self, directory: str) -> List[Tuple[Path, str]]:
        """Analyze imports in a directory."""
        imports = []
        dir_path = Path(directory)

        if not dir_path.exists():
            return imports

        for file_path in dir_path.rglob("*.py"):
            try:
                content = file_path.read_text(encoding="utf-8")
                tree = ast.parse(content)

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.append((file_path, alias.name))
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imports.append((file_path, node.module))
            except Exception as e:
                logger.warning(f"Could not analyze {file_path}: {e}")
                continue

        return imports

    def check_domain_layer_dependencies(self) -> None:
        """Check that domain layer doesn't depend on outer layers."""
        domain_imports = self.analyze_imports("src/domain")
        self.layer_imports["domain"] = domain_imports

        for file_path, import_name in domain_imports:
            if not import_name:
                continue

            # Domain should not import from infrastructure or interface layers
            if any(layer in import_name for layer in ["src.infrastructure", "src.interface"]):
                self.violations.append(
                    f"{file_path}: Domain layer importing from outer layer: {import_name}"
                )

            # Domain should not import from application layer
            if "src.application" in import_name:
                self.violations.append(
                    f"{file_path}: Domain layer importing from Application layer: {import_name}"
                )

    def check_application_layer_dependencies(self) -> None:
        """Check that application layer doesn't depend on interface layer."""
        app_imports = self.analyze_imports("src/application")
        self.layer_imports["application"] = app_imports

        for file_path, import_name in app_imports:
            if not import_name:
                continue

            # Application should not import from interface layer
            if "src.interface" in import_name:
                self.violations.append(
                    f"{file_path}: Application layer importing from Interface layer: {import_name}"
                )

    def check_infrastructure_layer_dependencies(self) -> None:
        """Check infrastructure layer dependencies."""
        infra_imports = self.analyze_imports("src/infrastructure")
        self.layer_imports["infrastructure"] = infra_imports

        for file_path, import_name in infra_imports:
            if not import_name:
                continue

            # Infrastructure should not import from interface layer
            if "src.interface" in import_name:
                self.violations.append(
                    f"{file_path}: Infrastructure layer importing from Interface layer: {import_name}"
                )

    def check_interface_layer_dependencies(self) -> None:
        """Check interface layer dependencies (should be minimal)."""
        interface_imports = self.analyze_imports("src/interface")
        self.layer_imports["interface"] = interface_imports

        # Interface layer can import from any layer (it's the outermost)
        # But we can warn about excessive dependencies

    def analyze_circular_dependencies(self) -> None:
        """Check for circular dependencies between modules."""
        # This is a simplified check - could be improved
        module_imports = {}

        for layer, imports in self.layer_imports.items():
            for file_path, import_name in imports:
                if import_name.startswith("src."):
                    module_name = str(file_path).replace("/", ".").replace(".py", "")
                    if module_name not in module_imports:
                        module_imports[module_name] = set()
                    module_imports[module_name].add(import_name)

        # Simple circular dependency detection
        for module, imports in module_imports.items():
            for imported_module in imports:
                if imported_module in module_imports:
                    if module.replace("src.", "") in str(module_imports[imported_module]):
                        self.violations.append(
                            f"Potential circular dependency: {module} <-> {imported_module}"
                        )

    def check_dependency_rules(self, warn_only: bool = False) -> None:
        """Main validation method."""
        logger.info("Checking Clean Architecture dependency rules...")

        # Check each layer
        self.check_domain_layer_dependencies()
        self.check_application_layer_dependencies()
        self.check_infrastructure_layer_dependencies()
        self.check_interface_layer_dependencies()

        # Check for circular dependencies
        self.analyze_circular_dependencies()

        # Report findings
        self.report_findings(warn_only)

    def report_findings(self, warn_only: bool) -> None:
        """Report validation findings."""
        if self.violations:
            logger.warning("Clean Architecture violations detected:")
            logger.warning("=" * 70)
            for violation in self.violations:
                logger.warning(f"  {violation}")
            logger.warning("=" * 70)
            logger.info("Clean Architecture Rules:")
            logger.info("- Domain layer should not depend on outer layers")
            logger.info("- Application layer should not depend on Interface layer")
            logger.info(
                "- Dependencies should flow inward: Interface -> Infrastructure -> Application -> Domain"
            )
            logger.info("- Avoid circular dependencies between modules")

            if not warn_only:
                logger.error("Build failed due to architecture violations.")
                sys.exit(1)
            else:
                logger.warning("Build continues with warnings.")
        else:
            logger.info("Clean Architecture dependency rules are followed.")

        # Summary statistics
        logger.info("Architecture Analysis Summary:")
        for layer, imports in self.layer_imports.items():
            logger.info(f"  {layer.capitalize()} layer: {len(imports)} imports analyzed")

    def generate_dependency_report(self) -> Dict[str, List[str]]:
        """Generate a detailed dependency report."""
        report = {}

        for layer, imports in self.layer_imports.items():
            layer_deps = set()
            for _, import_name in imports:
                if import_name and import_name.startswith("src."):
                    layer_deps.add(import_name)
            report[layer] = sorted(list(layer_deps))

        return report


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Validate Clean Architecture dependency rules")
    parser.add_argument("--warn-only", action="store_true", help="Only warn, don't fail the build")
    parser.add_argument("--report", action="store_true", help="Generate detailed dependency report")

    args = parser.parse_args()

    validator = ArchitectureValidator()

    if args.report:
        validator.check_dependency_rules(warn_only=True)
        report = validator.generate_dependency_report()

        logger.info("DETAILED DEPENDENCY REPORT:")
        logger.info("=" * 50)
        for layer, deps in report.items():
            logger.info(f"{layer.upper()} LAYER DEPENDENCIES:")
            for dep in deps:
                logger.info(f"  - {dep}")
    else:
        validator.check_dependency_rules(args.warn_only)


if __name__ == "__main__":
    main()
