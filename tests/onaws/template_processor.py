"""Template processor for onaws integration tests.

Generates per-test configuration directories by copying the real config files
from ./config/ and applying scenario-specific overrides directly to the template
list.  This replaces the old placeholder-based approach and keeps test configs
aligned with the actual runtime format.

Flow:
    config/aws_templates.json  ──load_templates_from_path──►  raw dicts (with real image_id)
                               ──format_templates_for_generation──►  scheduler wire format
                               ──copy + override──►  run_templates/<test>/aws_templates.json
    config/config.json         ──merge overrides──►  run_templates/<test>/config.json
    config/default_config.json ──copy──────────────►  run_templates/<test>/default_config.json
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)

# Fields that can be overridden directly on each template entry
TEMPLATE_OVERRIDE_KEYS = {
    "providerApi",
    "provider_api",
    "priceType",
    "price_type",
    "fleetType",
    "fleet_type",
    "percentOnDemand",
    "percent_on_demand",
    "vmTypes",
    "vm_types",
    "instance_types",
    "vmType",
    "vm_type",
    "vmTypesOnDemand",
    "vmTypesPriority",
    "allocationStrategy",
    "allocation_strategy",
    "allocationStrategyOnDemand",
    "allocation_strategy_on_demand",
    "abisInstanceRequirements",
    "abis_instance_requirements",
    "maxSpotPrice",
    "max_spot_price",
    "instanceTags",
    "instance_tags",
    "maxNumber",
    "max_number",
    "fleetRole",
    "fleet_role",
}

# Canonical camelCase → snake_case key mappings for template override keys
_CAMEL_TO_SNAKE: dict[str, str] = {
    "providerApi": "provider_api",
    "priceType": "price_type",
    "fleetType": "fleet_type",
    "percentOnDemand": "percent_on_demand",
    "vmTypes": "vm_types",
    "vmType": "vm_type",
    "allocationStrategy": "allocation_strategy",
    "allocationStrategyOnDemand": "allocation_strategy_on_demand",
    "abisInstanceRequirements": "abis_instance_requirements",
    "maxSpotPrice": "max_spot_price",
    "instanceTags": "instance_tags",
    "maxNumber": "max_number",
    "fleetRole": "fleet_role",
}
_SNAKE_TO_CAMEL: dict[str, str] = {v: k for k, v in _CAMEL_TO_SNAKE.items()}


def _detect_template_format(tmpl: dict) -> str:
    """Return 'camel' if template uses camelCase keys, 'snake' if snake_case."""
    if "provider_api" in tmpl:
        return "snake"
    if "providerApi" in tmpl:
        return "camel"
    if "template_id" in tmpl:
        return "snake"
    return "camel"


def _normalize_key(key: str, target_fmt: str) -> str:
    """Return key normalized to target_fmt ('camel' or 'snake'), unchanged if no mapping."""
    if target_fmt == "snake":
        return _CAMEL_TO_SNAKE.get(key, key)
    return _SNAKE_TO_CAMEL.get(key, key)



class TemplateProcessor:
    """Generates per-test config directories from the real project config files."""

    def __init__(self, base_dir: str | None = None):
        resolved: Path
        if base_dir is None:
            resolved = Path(__file__).parent
        else:
            resolved = Path(base_dir)

        self.base_dir = resolved
        self.config_source_dir = resolved.parent.parent / "config"
        self.run_templates_dir = resolved / "run_templates"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_test_templates(
        self,
        test_name: str,
        overrides: dict | None = None,
        metrics_config: dict | None = None,
        # Legacy parameters accepted but ignored
        base_template: str | None = None,
        awsprov_base_template: str | None = None,
    ) -> None:
        """Generate a per-test config directory with overridden templates.

        Args:
            test_name: Directory name under run_templates/
            overrides: Scenario overrides (providerApi, priceType, scheduler, etc.)
            metrics_config: Optional metrics configuration to inject into config.json
        """
        overrides = overrides or {}
        scheduler_type = overrides.get("scheduler", "hostfactory")

        # Prepare output directory
        test_dir = self.run_templates_dir / test_name
        test_dir.mkdir(parents=True, exist_ok=True)

        # 1. Generate aws_templates.json (with overrides applied)
        # Try programmatic generation first; fall back to filesystem copy
        try:
            templates_data = self.generate_templates_programmatically(scheduler_type)
            log.debug("Generated templates programmatically from handler classmethods")
        except Exception as exc:
            log.warning(
                "Programmatic template generation failed (%s), falling back to filesystem", exc
            )
            templates_data = self._load_json(self._find_templates_source())
        self._apply_template_overrides(templates_data, overrides)
        template_filename = "aws_templates.json"
        config_dir = test_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(config_dir / template_filename, templates_data)
        log.info(
            "Generated %s with %d templates",
            template_filename,
            len(templates_data.get("templates", [])),
        )

        # 2. Generate config.json (with scheduler/provider overrides)
        config_dir = test_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_data = self._load_json(self.config_source_dir / "config.json")
        self._apply_config_overrides(config_data, overrides, scheduler_type)
        self._set_storage_paths(config_data, test_dir)
        if metrics_config:
            metrics_dir = test_dir / "metrics"
            metrics_dir.mkdir(parents=True, exist_ok=True)
            mc = metrics_config.copy()
            if not mc.get("metrics_dir"):
                mc["metrics_dir"] = str(metrics_dir)
            config_data["metrics"] = mc
        self._write_json(config_dir / "config.json", config_data)

        # 3. Copy default_config.json as-is
        default_config_src = self.config_source_dir / "default_config.json"
        if default_config_src.exists():
            shutil.copy2(default_config_src, config_dir / "default_config.json")

        print(f"Generated test config in {test_dir}")

    def cleanup_test_templates(self, test_name: str) -> None:
        """Remove generated test directory."""
        test_dir = self.run_templates_dir / test_name
        if test_dir.exists():
            shutil.rmtree(test_dir)
            print(f"Cleaned up {test_dir}")

    def generate_combined_templates(
        self,
        test_name: str,
        template_configs: list[dict],
        scheduler_type: str = "hostfactory",
    ) -> None:
        """Generate a config directory with multiple custom templates combined.

        Used by multi-termination tests that need several templates with
        different providerApi/fleetType settings in a single config dir.

        Args:
            test_name: Directory name under run_templates/
            template_configs: List of dicts with 'template_name' and 'overrides' keys.
            scheduler_type: Scheduler type - "hostfactory" (camelCase) or "default" (snake_case).
        """
        test_dir = self.run_templates_dir / test_name
        test_dir.mkdir(parents=True, exist_ok=True)

        # Load source templates and config
        try:
            source_templates = self.generate_templates_programmatically(scheduler_type)
        except Exception as exc:
            log.warning(
                "Programmatic template generation failed (%s), falling back to filesystem", exc
            )
            source_templates = self._load_json(self._find_templates_source())
        config_data = self._load_json(self.config_source_dir / "config.json")

        # Build combined template list: one entry per template_config,
        # cloned from the first source template with overrides applied
        # Key name depends on scheduler wire format: camelCase for HF, snake_case for default
        template_id_key = "templateId" if scheduler_type == "hostfactory" else "template_id"
        base_entry = source_templates.get("templates", [{}])[0]
        combined = []
        for tc in template_configs:
            entry = dict(base_entry)
            entry[template_id_key] = tc["template_name"]
            for k, v in tc.get("overrides", {}).items():
                if k in TEMPLATE_OVERRIDE_KEYS:
                    entry[k] = v
            combined.append(entry)

        config_dir = test_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(config_dir / "aws_templates.json", {"scheduler_type": scheduler_type, "templates": combined})
        self._set_storage_paths(config_data, test_dir)
        self._write_json(config_dir / "config.json", config_data)

        default_config_src = self.config_source_dir / "default_config.json"
        if default_config_src.exists():
            shutil.copy2(default_config_src, config_dir / "default_config.json")

        print(f"Generated combined config with {len(combined)} templates in {test_dir}")

    # ------------------------------------------------------------------
    # Programmatic template generation
    # ------------------------------------------------------------------

    @staticmethod
    def generate_templates_programmatically(scheduler_type: str = "hostfactory") -> Dict[str, Any]:
        """Load templates from the real config file using the production scheduler path.

        Uses load_templates_from_path (same as runtime) so templates have real image_id,
        subnet_ids, security_group_ids etc from the generated config/aws_templates.json.
        Then formats via format_templates_for_generation for the correct scheduler wire format.

        Returns:
            {"templates": [...]} dict in the format expected by the scheduler
        """
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

        strategy = TemplateProcessor._make_strategy(scheduler_type)

        # Find the real generated templates file in config/
        config_dir = Path(__file__).parent.parent.parent / "config"
        templates_file = config_dir / "aws_templates.json"
        if not templates_file.exists():
            raise FileNotFoundError(
                f"No generated templates found at {templates_file}. "
                "Run 'orb templates generate' first."
            )

        # Load via production path — preserves image_id, subnet_ids, etc.
        raw_dicts = strategy.load_templates_from_path(str(templates_file))
        formatted = strategy.format_templates_for_generation(raw_dicts)

        return {"scheduler_type": scheduler_type, "templates": formatted}

    @staticmethod
    def _make_strategy(scheduler_type: str):
        """Return the production scheduler strategy instance for the given type."""
        if scheduler_type == "hostfactory":
            from infrastructure.scheduler.hostfactory.hostfactory_strategy import (
                HostFactorySchedulerStrategy,
            )

            return HostFactorySchedulerStrategy()
        else:
            from infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

            return DefaultSchedulerStrategy()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_templates_source(self) -> Path:
        """Find the templates source file in config/."""
        for name in ("aws_templates.json", "templates.json"):
            path = self.config_source_dir / name
            if path.exists():
                return path
        raise FileNotFoundError(
            f"No template file found in {self.config_source_dir}. "
            "Run 'orb init && orb templates generate' first."
        )

    def _apply_template_overrides(self, templates_data: Dict[str, Any], overrides: dict) -> None:
        """Apply scenario overrides to every template, normalizing key case to match template format."""
        template_overrides = {k: v for k, v in overrides.items() if k in TEMPLATE_OVERRIDE_KEYS}
        if not template_overrides:
            return

        for tmpl in templates_data.get("templates", []):
            fmt = _detect_template_format(tmpl)
            for k, v in template_overrides.items():
                normalized_key = _normalize_key(k, fmt)
                opposite_key = _normalize_key(k, "snake" if fmt == "camel" else "camel")
                if opposite_key != normalized_key and opposite_key in tmpl:
                    del tmpl[opposite_key]
                tmpl[normalized_key] = v

    def _apply_config_overrides(
        self, config_data: Dict[str, Any], overrides: dict, scheduler_type: str
    ) -> None:
        """Apply scheduler type and other config-level overrides."""
        # Set scheduler type
        config_data.setdefault("scheduler", {})
        config_data["scheduler"]["type"] = scheduler_type

        # Apply region/profile overrides if provided
        if "region" in overrides or "profile" in overrides:
            providers = config_data.get("provider", {}).get("providers", [])
            for provider in providers:
                cfg = provider.setdefault("config", {})
                if "region" in overrides:
                    cfg["region"] = overrides["region"]
                if "profile" in overrides:
                    cfg["profile"] = overrides["profile"]

    @staticmethod
    def _set_storage_paths(config_data: Dict[str, Any], test_dir: Path) -> None:
        """Point storage paths at the test directory to isolate parallel runs."""
        data_dir = str(test_dir / "data")
        config_data.setdefault("storage", {})
        config_data["storage"]["default_storage_path"] = data_dir
        config_data["storage"].setdefault("json_strategy", {})
        config_data["storage"]["json_strategy"]["base_path"] = data_dir

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")
        with open(path) as f:
            return json.load(f)

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


if __name__ == "__main__":
    import sys

    processor = TemplateProcessor()
    name = sys.argv[1] if len(sys.argv) > 1 else "manual_test"
    processor.generate_test_templates(name)
    print(f"Generated test templates in: {processor.run_templates_dir / name}")
