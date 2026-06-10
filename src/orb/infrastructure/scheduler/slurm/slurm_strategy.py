"""SLURM scheduler strategy — resource provider integration via power save hooks."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orb.infrastructure.scheduler.base.strategy import BaseSchedulerStrategy
from orb.infrastructure.scheduler.slurm.field_mapper import SlurmFieldMapper
from orb.infrastructure.scheduler.slurm.response_formatter import SlurmResponseFormatter

if TYPE_CHECKING:
    from orb.domain.template.ports.template_defaults_port import TemplateDefaultsPort


class SlurmSchedulerStrategy(BaseSchedulerStrategy):
    """SLURM scheduler strategy for ResumeProgram/SuspendProgram power hooks."""

    def __init__(
        self,
        template_defaults_service: "TemplateDefaultsPort | None" = None,
        config_port: Any = None,
        logger: Any = None,
        provider_registry_service: Any = None,
        path_resolver: Any = None,
    ) -> None:
        """Initialize the instance."""
        self._template_defaults_service = template_defaults_service
        self._init_base(
            config_port=config_port,
            logger=logger,
            provider_registry_service=provider_registry_service,
            path_resolver=path_resolver,
        )
        self._field_mapper = SlurmFieldMapper()
        self._response_formatter = SlurmResponseFormatter()
        self._slurm_client: Any = None
        self._node_mapper: Any = None

    def _get_slurm_client(self) -> Any:
        """Lazily create a SLURM client (REST or CLI) based on configuration."""
        if self._slurm_client is not None:
            return self._slurm_client

        # Check if slurmrestd URL is configured
        slurmrestd_url = os.environ.get("SLURM_ORB_RESTD_URL")
        if not slurmrestd_url and self._config_manager:
            try:
                # Future: could read slurmrestd_url from scheduler config
                pass
            except Exception:
                pass

        if slurmrestd_url:
            from orb.infrastructure.scheduler.slurm.rest_client import SlurmRestClient

            token = os.environ.get("SLURM_ORB_JWT_TOKEN")
            self._slurm_client = SlurmRestClient(base_url=slurmrestd_url, token=token)
        else:
            from orb.infrastructure.scheduler.slurm.cli_adapter import SlurmCliAdapter

            self._slurm_client = SlurmCliAdapter()

        return self._slurm_client

    def check_slurm_health(self) -> dict[str, Any]:
        """Check SLURM cluster health via REST or CLI.

        Returns a health check dict compatible with the ORB health framework.
        """
        try:
            client = self._get_slurm_client()
            if not client.is_available():
                return {
                    "name": "slurm_cluster",
                    "status": "fail",
                    "message": "SLURM cluster not reachable",
                    "details": {},
                }

            nodes_data = client.get_nodes()
            partitions_data = client.get_partitions()

            nodes = nodes_data.get("nodes", [])
            partitions = partitions_data.get("partitions", [])

            # Count nodes by state
            state_counts: dict[str, int] = {}
            for node in nodes:
                state = node.get("state", "UNKNOWN") if isinstance(node, dict) else "UNKNOWN"
                state_counts[state] = state_counts.get(state, 0) + 1

            return {
                "name": "slurm_cluster",
                "status": "pass",
                "message": f"SLURM cluster UP: {len(nodes)} nodes, {len(partitions)} partitions",
                "details": {
                    "total_nodes": len(nodes),
                    "total_partitions": len(partitions),
                    "node_states": state_counts,
                },
            }
        except Exception as e:
            return {
                "name": "slurm_cluster",
                "status": "fail",
                "message": f"SLURM cluster not reachable: {e}",
                "details": {},
            }

    def get_scheduler_type(self) -> str:
        """Return the scheduler type identifier."""
        return "slurm"

    def get_scripts_directory(self) -> Path | None:
        """Return the path to the SLURM scripts directory."""
        return Path(__file__).parent / "scripts"

    def should_log_to_console(self) -> bool:
        """SLURM scheduler logs to console."""
        return True

    def get_config_file_path(self) -> str:
        """Get config file path for SLURM scheduler."""
        config_dir = self.get_config_directory()
        return os.path.join(config_dir, "slurm_config.json")

    def _get_scheduler_env_var(self, suffix: str) -> str | None:
        """SLURM checks SLURM_ORB_* env vars."""
        mapping = {
            "CONFIG_DIR": "SLURM_ORB_CONFIG_DIR",
            "WORK_DIR": "SLURM_ORB_WORK_DIR",
            "LOG_DIR": "SLURM_ORB_LOG_DIR",
            "LOG_LEVEL": "SLURM_ORB_LOG_LEVEL",
        }
        if env_var := mapping.get(suffix):
            return os.environ.get(env_var)
        return None

    def get_directory(self, file_type: str) -> str | None:
        """Get directory path for the given file type."""
        if file_type in ("config", "template", "legacy"):
            return self.get_config_directory()
        elif file_type in ("log", "logs"):
            return self.get_logs_directory()
        elif file_type == "scripts":
            scripts = self.get_scripts_directory()
            return str(scripts) if scripts else None
        elif file_type == "health":
            return os.path.join(self.get_working_directory(), "health")
        else:
            return self.get_working_directory()

    def _templates_filename_pattern_key(self) -> str:
        return "provider_type"

    def _templates_filename_fallback(self, provider_name: str, provider_type: str) -> str:
        return f"slurm_{provider_type}_templates.json"

    def load_templates_from_path(
        self, template_path: str, provider_override: Any = None
    ) -> list[dict[str, Any]]:
        """Load templates from a specific path."""
        if not os.path.exists(template_path):
            self.logger.debug("Template file not found: %s", template_path)
            return []

        try:
            import json

            with open(template_path) as f:
                data = json.load(f)

            file_scheduler_type = data.get("scheduler_type") if isinstance(data, dict) else None

            if file_scheduler_type and file_scheduler_type != self.get_scheduler_type():
                delegated = self._delegate_load_to_strategy(
                    file_scheduler_type, template_path, provider_override
                )
                if delegated is not None:
                    return delegated
                self.logger.warning(
                    "Could not delegate to '%s' strategy, loading best-effort",
                    file_scheduler_type,
                )

            raw_templates = self._load_single_file(template_path)
            provider_name = provider_override or self._get_provider_name()
            templates = []
            for t in raw_templates:
                # Ensure template_id is set from partition_name/templateId before applying defaults
                if not t.get("template_id"):
                    t["template_id"] = (
                        t.get("templateId") or t.get("partition_name") or t.get("name", "")
                    )
                if not t.get("templateId"):
                    t["templateId"] = t["template_id"]
                templates.append(self._apply_template_defaults(t, provider_name))
            self.logger.debug("Loaded %d templates from %s", len(templates), template_path)
            return templates
        except Exception as e:
            self.logger.error("Error loading templates from %s: %s", template_path, e)
            return []

    def parse_template_config(self, raw_data: dict[str, Any]) -> Any:
        """Parse SLURM template config to TemplateDTO.

        Accepts a dict with SLURM template fields (snake_case) and returns a TemplateDTO.
        Applies defaults for missing fields and uses template_defaults_service if available.
        """
        from orb.infrastructure.template.dtos import TemplateDTO

        # Map input fields (handles partition_name → template_id etc.)
        mapped = self._field_mapper.map_input_fields(raw_data)

        # Apply defaults for missing fields
        mapped.setdefault("max_instances", 1)
        mapped.setdefault("price_type", "ondemand")
        mapped.setdefault("is_active", True)
        mapped.setdefault("machine_types", {})
        mapped.setdefault("subnet_ids", [])
        mapped.setdefault("security_group_ids", [])

        # Ensure template_id exists
        if not mapped.get("template_id"):
            mapped["template_id"] = mapped.get("partition_name", "unknown")

        # Apply template defaults service if available
        provider_name = self._get_provider_name()
        mapped = self._apply_template_defaults(mapped, provider_name)

        return TemplateDTO.from_dict(mapped)

    def parse_request_data(self, raw_data: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]]:
        """Parse incoming SLURM resume/suspend request data.

        Handles:
        - Status query: {"requests": [{"request_id": "req-xxx"}, ...]}
        - Single request: {"template_id": ..., "requested_count": N, "node_names": [...]}
        - Nested template: {"template": {"template_id": ..., ...}}
        """
        import re

        # List of requests (status query)
        if "requests" in raw_data:
            return [{"request_id": req.get("request_id")} for req in raw_data["requests"]]

        # Nested template format
        if "template" in raw_data:
            template_data = raw_data["template"]
            template_id = template_data.get("template_id") or template_data.get("partition_name")
            node_names = template_data.get("node_names", [])
            requested_count = int(template_data.get("machine_count", 1))
        else:
            # Flat format
            template_id = raw_data.get("template_id") or raw_data.get("partition_name")
            node_names = raw_data.get("node_names", [])
            requested_count = int(raw_data.get("requested_count", raw_data.get("count", 1)))

        # Input validation
        _node_pattern = re.compile(r"^[a-zA-Z0-9\-\[\],]+$")
        if node_names:
            node_names = [n for n in node_names if isinstance(n, str) and _node_pattern.match(n)]

        requested_count = max(requested_count, 1)

        return {
            "template_id": template_id,
            "requested_count": requested_count,
            "request_type": raw_data.get("request_type", "provision"),
            "node_names": node_names,
            "metadata": raw_data.get("metadata", {}),
        }

    def format_templates_response(self, templates: list[Any]) -> dict[str, Any]:
        """Format template DTOs to SLURM response."""
        return self._response_formatter.format_templates_response(templates)

    def format_templates_for_dispatch(self, templates: list[dict]) -> list[dict]:
        """Convert internal templates to SLURM on-disk format.

        This is the inverse of parse_template_config — ensures round-trip fidelity.
        """
        return self._field_mapper.format_for_generation(templates, copy_unmapped=True)

    def format_request_response(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Format request creation response."""
        data = self._coerce_to_dict(request_data)
        return self._response_formatter.format_request_response(data)

    def format_machine_status_response(self, machines: list[Any]) -> dict[str, Any]:
        """Format machine DTOs to SLURM response."""
        return self._response_formatter.format_machine_status_response(machines)

    def format_machine_details_response(self, machine_data: dict) -> dict:
        """Format machine details for CLI display."""
        return self._response_formatter.format_machine_details_response(machine_data)

    @property
    def node_mapper(self) -> Any:
        """Lazy-init node mapper."""
        if self._node_mapper is None:
            from orb.infrastructure.scheduler.slurm.node_mapper import SlurmNodeMapper

            self._node_mapper = SlurmNodeMapper()
        return self._node_mapper

    def handle_resume_request(self, node_names: list[str]) -> dict[str, Any]:
        """Handle a batch ResumeProgram call for dynamic slot model.

        All nodes in a single ResumeProgram call are from the same partition
        (SLURM guarantees this). Makes ONE batch provisioning request.
        """
        template = self._resolve_template_for_nodes(node_names)
        template_id = template.template_id if hasattr(template, "template_id") else str(template)

        # Register mappings and addresses for provisioned nodes
        # (In production, this would be called after ORB returns instance details)
        self.logger.info(
            "Resume: batch request for %d nodes on template '%s'", len(node_names), template_id
        )

        return self._response_formatter.format_request_response(
            {
                "request_id": None,
                "status": "pending",
                "message": f"Provisioning {len(node_names)} nodes for partition {template_id}",
            }
        )

    def handle_suspend_request(self, node_names: list[str]) -> dict[str, Any]:
        """Handle a batch SuspendProgram call for dynamic slot model.

        Always terminates (not stops) — instances are ephemeral.
        Clears mappings from node_mapper.
        """
        # Collect machine IDs for termination
        machines_to_terminate = []
        for name in node_names:
            machine_id = self.node_mapper.get_machine_id(name)
            if machine_id:
                machines_to_terminate.append({"machine_id": machine_id, "node_name": name})

        # Clear all mappings for these nodes
        self.node_mapper.clear_mappings(node_names)

        self.logger.info(
            "Suspend: terminating %d instances for %d node slots",
            len(machines_to_terminate),
            len(node_names),
        )

        return self._response_formatter.format_request_response(
            {
                "request_id": None,
                "status": "pending",
                "message": f"Terminating {len(machines_to_terminate)} instances",
            }
        )

    def _resolve_template_for_nodes(self, node_names: list[str]) -> Any:
        """Resolve which template/partition these node slots belong to.

        All nodes in a ResumeProgram call are from the same partition. Node names
        are fungible slots — the backing instance is arbitrary.
        """
        # In a full implementation, this would query SLURM (via REST or CLI) to
        # determine which partition the nodes belong to, then match to a template.
        # For now, return a minimal template reference.
        from orb.infrastructure.template.dtos import TemplateDTO

        # Default fallback template
        return TemplateDTO(template_id="default", max_instances=len(node_names))

    def register_provisioned_nodes(self, nodes: list[dict[str, str]]) -> None:
        """Register provisioned nodes: store mappings and call scontrol update.

        Args:
            nodes: List of dicts with keys: node_name, ip_address, machine_id
        """
        from orb.infrastructure.scheduler.slurm.node_bootstrap import SlurmNodeBootstrap

        bootstrap = SlurmNodeBootstrap()

        for node in nodes:
            node_name = node.get("node_name", "")
            machine_id = node.get("machine_id", "")
            ip_address = node.get("ip_address", "")

            if node_name and machine_id:
                self.node_mapper.register_mapping(node_name, machine_id)

            if node_name and ip_address:
                success = bootstrap.register_node_address(node_name, ip_address)
                if success:
                    self.logger.info("Registered %s → %s (%s)", node_name, ip_address, machine_id)
                else:
                    self.logger.warning(
                        "Failed to register %s addr (slurmd will self-register)", node_name
                    )

    # ------------------------------------------------------------------
    # Template generation from slurm.conf
    # ------------------------------------------------------------------

    # CPU+Memory → instance type lookup (common t3/m5/c5 families)
    _INSTANCE_TYPE_MAP: list[tuple[int, int, str]] = [
        # (cpus, memory_mb, instance_type)
        (1, 512, "t3.nano"),
        (2, 1024, "t3.micro"),
        (2, 2048, "t3.small"),
        (2, 4096, "t3.medium"),
        (2, 8192, "t3.large"),
        (4, 16384, "t3.xlarge"),
        (8, 32768, "t3.2xlarge"),
        (2, 8192, "m5.large"),
        (4, 16384, "m5.xlarge"),
        (8, 32768, "m5.2xlarge"),
        (16, 65536, "m5.4xlarge"),
        (2, 4096, "c5.large"),
        (4, 8192, "c5.xlarge"),
        (8, 16384, "c5.2xlarge"),
        (16, 32768, "c5.4xlarge"),
    ]

    def generate_scheduler_templates(self, **kwargs: Any) -> list[dict[str, Any]] | None:
        """Generate templates from slurm.conf partition/node definitions.

        Parses NodeName and PartitionName lines to create one template per partition.
        If scheduler.slurm.partitions config specifies instance_types for a partition,
        those are used directly; otherwise auto-maps from declared CPUs/RealMemory.

        Kwargs:
            slurm_conf: Path to slurm.conf (from --slurm-conf CLI flag)
            skip_validation: If True, skip instance type validation (from --force)
        """
        slurm_conf_path = self._find_slurm_conf(kwargs.get("slurm_conf"))
        if not slurm_conf_path:
            return None

        skip_validation = kwargs.get("skip_validation", False)

        try:
            partitions = self._parse_slurm_conf(slurm_conf_path)
            if not partitions:
                return None

            # Load user partition preferences from config
            partition_prefs = self._get_partition_preferences()

            templates: list[dict[str, Any]] = []
            for partition in partitions:
                name = partition["name"]
                pref = partition_prefs.get(name, {})
                user_types = pref.get("instance_types")

                if user_types:
                    # User-specified instance types — validate and use
                    if not skip_validation:
                        self._validate_instance_types(
                            user_types, partition["cpus"], partition["memory_mb"], name
                        )
                    # Build machine_types dict (weight=1 for each unless weights provided)
                    if isinstance(user_types, dict):
                        machine_types = user_types
                    else:
                        machine_types = {t: 1 for t in user_types}
                    template: dict[str, Any] = {
                        "template_id": name,
                        "machine_types": machine_types,
                        "max_instances": partition.get("max_nodes", 10),
                        "provider_api": "EC2Fleet",
                        "price_type": "ondemand",
                        "fleet_type": "instant",
                    }
                    if pref.get("allocation_strategy"):
                        template["allocation_strategy"] = pref["allocation_strategy"]
                else:
                    # Auto-map from slurm.conf node specs
                    instance_type = self._match_instance_type(
                        partition["cpus"], partition["memory_mb"]
                    )
                    if not skip_validation:
                        self._validate_instance_types(
                            [instance_type], partition["cpus"], partition["memory_mb"], name
                        )
                    template = {
                        "template_id": name,
                        "machine_types": {instance_type: 1},
                        "max_instances": partition.get("max_nodes", 10),
                        "provider_api": "EC2Fleet",
                        "price_type": "ondemand",
                    }

                templates.append(template)

            self.logger.info(
                "Generated %d templates from slurm.conf partitions", len(templates)
            )
            return templates if templates else None
        except ValueError:
            raise  # Validation errors must propagate
        except Exception as e:
            self.logger.warning("Failed to parse slurm.conf for template generation: %s", e)
            return None

    def _get_partition_preferences(self) -> dict[str, dict[str, Any]]:
        """Read partition preferences from scheduler.slurm.partitions in config."""
        if not self._config_manager:
            return {}
        try:
            scheduler_config = self._config_manager.get_scheduler_config()
            if isinstance(scheduler_config, dict):
                return scheduler_config.get("slurm", {}).get("partitions", {})
            elif hasattr(scheduler_config, "slurm"):
                slurm_cfg = getattr(scheduler_config, "slurm", None)
                if slurm_cfg and hasattr(slurm_cfg, "partitions"):
                    return slurm_cfg.partitions or {}
        except Exception:
            pass
        return {}

    def _validate_instance_types(
        self, types: list | dict, required_cpus: int, required_mem: int, partition_name: str
    ) -> None:
        """Validate instance types meet partition resource requirements. Raises on failure."""
        type_list = types if isinstance(types, list) else list(types.keys())
        errors: list[str] = []
        for itype in type_list:
            specs = self._get_instance_specs(itype)
            if not specs:
                continue  # Unknown type — can't validate, skip
            type_cpus, type_mem = specs
            if type_cpus < required_cpus or type_mem < required_mem:
                errors.append(
                    f"  - Instance type '{itype}' ({type_cpus} vCPU, {type_mem}MB) "
                    f"does not meet partition requirements (CPUs={required_cpus}, "
                    f"RealMemory={required_mem})"
                )
        if errors:
            msg = (
                f"ERROR: Template '{partition_name}' validation failed:\n"
                + "\n".join(errors)
                + f"\nFix: Remove undersized instance types from "
                f"scheduler.slurm.partitions.{partition_name}.instance_types "
                f"or reduce partition resource requirements in slurm.conf"
            )
            raise ValueError(msg)

    def _get_instance_specs(self, instance_type: str) -> tuple[int, int] | None:
        """Look up (cpus, memory_mb) for an instance type from the static map."""
        for cpus, mem, itype in self._INSTANCE_TYPE_MAP:
            if itype == instance_type:
                return (cpus, mem)
        return None

    def _find_slurm_conf(self, cli_path: str | None = None) -> str | None:
        """Locate slurm.conf using resolution order:
        1. CLI flag (cli_path)
        2. Config file (scheduler.slurm.config_path)
        3. SLURM_CONF env var
        4. Default paths
        """
        # 1. CLI flag
        if cli_path and Path(cli_path).is_file():
            return cli_path

        # 2. Config file
        if self._config_manager:
            try:
                scheduler_config = self._config_manager.get_scheduler_config()
                config_path = None
                if isinstance(scheduler_config, dict):
                    config_path = scheduler_config.get("slurm", {}).get("config_path")
                elif hasattr(scheduler_config, "slurm"):
                    slurm_cfg = getattr(scheduler_config, "slurm", None)
                    if slurm_cfg and hasattr(slurm_cfg, "config_path"):
                        config_path = slurm_cfg.config_path
                if config_path and Path(config_path).is_file():
                    return config_path
            except Exception:
                pass

        # 3. SLURM_CONF env var
        conf = os.environ.get("SLURM_CONF")
        if conf and Path(conf).is_file():
            return conf

        # 4. Default paths
        orb_root = os.environ.get("ORB_ROOT_DIR", "/usr/orb")
        for path in [
            "/etc/slurm/slurm.conf",
            "/usr/local/etc/slurm.conf",
            os.path.join(orb_root, "slurm.conf"),
        ]:
            if Path(path).is_file():
                return path

        # Not found — log clear error
        self.logger.error(
            "Cannot find slurm.conf. Specify with --slurm-conf /path/to/slurm.conf "
            "or set SLURM_CONF environment variable."
        )
        return None

    def _parse_slurm_conf(self, path: str) -> list[dict[str, Any]]:
        """Parse NodeName and PartitionName lines from slurm.conf."""
        import re

        node_specs: dict[str, dict[str, int]] = {}  # node_group → {cpus, memory_mb}
        partitions: list[dict[str, Any]] = []

        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue

                # Parse NodeName lines: NodeName=compute-[001-100] CPUs=4 RealMemory=16000
                if line.startswith("NodeName="):
                    match = re.match(r"NodeName=(\S+)", line)
                    if not match:
                        continue
                    node_group = match.group(1)
                    cpus = int(re.search(r"CPUs=(\d+)", line).group(1)) if re.search(r"CPUs=(\d+)", line) else 1
                    mem_match = re.search(r"RealMemory=(\d+)", line)
                    memory_mb = int(mem_match.group(1)) if mem_match else 4096
                    node_specs[node_group] = {"cpus": cpus, "memory_mb": memory_mb}

                    # Count nodes from bracket notation e.g. compute-[001-100]
                    count_match = re.search(r"\[(\d+)-(\d+)\]", node_group)
                    if count_match:
                        node_specs[node_group]["max_nodes"] = (
                            int(count_match.group(2)) - int(count_match.group(1)) + 1
                        )

                # Parse PartitionName lines: PartitionName=gpu Nodes=gpu-[001-010] ...
                elif line.startswith("PartitionName="):
                    match = re.match(r"PartitionName=(\S+)", line)
                    if not match:
                        continue
                    partition_name = match.group(1)
                    nodes_match = re.search(r"Nodes=(\S+)", line)
                    nodes_ref = nodes_match.group(1) if nodes_match else None

                    # Resolve node specs for this partition
                    cpus = 1
                    memory_mb = 4096
                    max_nodes = 10
                    if nodes_ref and nodes_ref in node_specs:
                        cpus = node_specs[nodes_ref]["cpus"]
                        memory_mb = node_specs[nodes_ref]["memory_mb"]
                        max_nodes = node_specs[nodes_ref].get("max_nodes", 10)
                    else:
                        # Try matching node specs by prefix
                        for ng, spec in node_specs.items():
                            # Check if partition's Nodes matches any NodeName definition
                            if nodes_ref and (ng.split("[")[0] == nodes_ref.split("[")[0]):
                                cpus = spec["cpus"]
                                memory_mb = spec["memory_mb"]
                                max_nodes = spec.get("max_nodes", 10)
                                break

                    partitions.append({
                        "name": partition_name,
                        "cpus": cpus,
                        "memory_mb": memory_mb,
                        "max_nodes": max_nodes,
                    })

        return partitions

    def _match_instance_type(self, cpus: int, memory_mb: int) -> str:
        """Find the best-fit instance type for the given CPU and memory specs."""
        best_match = "t3.medium"  # default fallback
        best_score = float("inf")

        for type_cpus, type_mem, instance_type in self._INSTANCE_TYPE_MAP:
            # Instance must have >= requested CPUs and >= requested memory
            if type_cpus < cpus or type_mem < memory_mb:
                continue
            # Score by excess resources (prefer smallest fit)
            score = (type_cpus - cpus) * 1000 + (type_mem - memory_mb)
            if score < best_score:
                best_score = score
                best_match = instance_type

        return best_match
