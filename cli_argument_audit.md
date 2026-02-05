# CLI Argument Usage Audit

**Date:** 2026-02-05  
**File:** src/cli/main.py  
**Analysis:** Complete argument duplication and location mapping

## Global Arguments (Lines 360-390)

### Main Parser Global Arguments:
```python
# Line 361: --config
parser.add_argument("--config", help="Configuration file path")

# Line 362-367: --log-level  
parser.add_argument(
    "--log-level",
    choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    default="INFO",
    help="Set logging level",
)

# Line 368-373: --format (GLOBAL)
parser.add_argument(
    "--format",
    choices=["json", "yaml", "table", "list"],
    default="json",
    help="Output format",
)

# Line 374: --output
parser.add_argument("--output", help="Output file (default: stdout)")

# Line 375: --quiet
parser.add_argument("--quiet", action="store_true", help="Suppress non-essential output")

# Line 376: --verbose
parser.add_argument("--verbose", action="store_true", help="Enable verbose output")

# Line 377-381: --dry-run (GLOBAL)
parser.add_argument(
    "--dry-run",
    action="store_true",
    help="Show what would be done without executing",
)

# Line 382-386: --scheduler (GLOBAL)
parser.add_argument(
    "--scheduler",
    choices=["default", "hostfactory", "hf"],
    help="Override scheduler strategy for this command",
)

# Line 387-388: --provider (GLOBAL)
parser.add_argument(
    "--provider", help="Override provider instance for this command (e.g., aws-prod, aws-dev)"
)

# Line 389-392: --completion
parser.add_argument(
    "--completion", choices=["bash", "zsh"], help="Generate shell completion script"
)

# Line 395-396: -f, --file (HostFactory compatibility)
parser.add_argument("-f", "--file", help="Input JSON file path (HostFactory compatibility)")

# Line 397: -d, --data (HostFactory compatibility)
parser.add_argument("-d", "--data", help="Input JSON data string (HostFactory compatibility)")
```

## Override Logic (Lines 880-910)

### Scheduler Override:
```python
# Lines 885-896
scheduler_override_active = False
if hasattr(args, "scheduler") and args.scheduler:
    try:
        from infrastructure.di.container import get_container
        from domain.base.ports.configuration_port import ConfigurationPort
        
        container = get_container()
        config = container.get(ConfigurationPort)
        config.override_scheduler_strategy(args.scheduler)
        scheduler_override_active = True
    except Exception as e:
        logger = get_logger(__name__)
        logger.warning("Failed to override scheduler strategy: %s", e)
```

### Provider Override:
```python
# Lines 898-909
provider_override_active = False
if hasattr(args, "provider") and args.provider:
    try:
        from infrastructure.di.container import get_container
        from domain.base.ports.configuration_port import ConfigurationPort
        
        container = get_container()
        config = container.get(ConfigurationPort)
        config.override_provider_instance(args.provider)
        provider_override_active = True
    except Exception as e:
        logger = get_logger(__name__)
        logger.warning("Failed to override provider instance: %s", e)
```

## Argument Duplication Analysis

### 1. --provider Argument Locations

#### Global (Line 387-388):
```python
parser.add_argument("--provider", help="Override provider instance for this command (e.g., aws-prod, aws-dev)")
```

#### Local Duplications:
1. **Infrastructure discover (Line 149):**
   ```python
   infra_discover.add_argument("--provider", help="Specific provider to discover")
   ```

2. **Infrastructure show (Line 158):**
   ```python
   infra_show.add_argument("--provider", help="Specific provider to show")
   ```

3. **Infrastructure validate (Line 167):**
   ```python
   infra_validate.add_argument("--provider", help="Specific provider to validate")
   ```

4. **Providers show (Line 184):**
   ```python
   providers_show.add_argument("--provider", help="Show specific provider details")
   ```

5. **Providers health (Line 191):**
   ```python
   providers_health.add_argument("--provider", help="Check specific provider health")
   ```

6. **Providers exec (Line 200):**
   ```python
   providers_exec.add_argument("--provider", help="Provider to execute operation on")
   ```

7. **Providers metrics (Line 207):**
   ```python
   providers_metrics.add_argument("--provider", help="Show metrics for specific provider")
   ```

8. **Templates generate (Line 311):**
   ```python
   templates_generate.add_argument("--provider", help="Generate for specific provider instance")
   ```

**TOTAL: 8 local duplications of global --provider argument**

### 2. --format Argument Locations

#### Global (Line 368-373):
```python
parser.add_argument(
    "--format",
    choices=["json", "yaml", "table", "list"],
    default="json",
    help="Output format",
)
```

#### Local Duplications:
1. **Machines list (Line 32-34):**
   ```python
   machines_list.add_argument(
       "--format", choices=["json", "yaml", "table", "list"], help="Output format"
   )
   ```

2. **Machines show (Line 44-46):**
   ```python
   machines_show.add_argument(
       "--format", choices=["json", "yaml", "table", "list"], help="Output format"
   )
   ```

3. **Requests list (Line 88-90):**
   ```python
   requests_list.add_argument(
       "--format", choices=["json", "yaml", "table", "list"], help="Output format"
   )
   ```

4. **Requests show (Line 96-98):**
   ```python
   requests_show.add_argument(
       "--format", choices=["json", "yaml", "table", "list"], help="Output format"
   )
   ```

5. **System status (Line 485-487):**
   ```python
   system_status.add_argument(
       "--format", choices=["json", "yaml", "table", "list"], help="Output format"
   )
   ```

6. **System metrics (Line 495-497):**
   ```python
   system_metrics.add_argument(
       "--format", choices=["json", "yaml", "table", "list"], help="Output format"
   )
   ```

7. **Providers list (Line 175-177):**
   ```python
   providers_list.add_argument(
       "--format", choices=["json", "yaml", "table", "list"], help="Output format"
   )
   ```

8. **Providers show (Line 182-184):**
   ```python
   providers_show.add_argument(
       "--format", choices=["json", "yaml", "table", "list"], help="Output format"
   )
   ```

9. **Providers health (Line 189-191):**
   ```python
   providers_health.add_argument(
       "--format", choices=["json", "yaml", "table", "list"], help="Output format"
   )
   ```

10. **Providers metrics (Line 205-207):**
    ```python
    providers_metrics.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    ```

11. **Templates list (Line 218-220):**
    ```python
    templates_list.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    ```

12. **Templates show (Line 230-232):**
    ```python
    templates_show.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    ```

13. **Config show (Line 540-542):**
    ```python
    config_show.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    ```

14. **Storage list (Line 580-582):**
    ```python
    storage_list.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    ```

15. **Storage show (Line 586-588):**
    ```python
    storage_show.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    ```

16. **Storage health (Line 600-602):**
    ```python
    storage_health.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    ```

17. **Storage metrics (Line 610-612):**
    ```python
    storage_metrics.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    ```

18. **Scheduler list (Line 620-622):**
    ```python
    scheduler_list.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    ```

19. **Scheduler show (Line 628-630):**
    ```python
    scheduler_show.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    ```

20. **Scheduler validate (Line 636-638):**
    ```python
    scheduler_validate.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    ```

21. **MCP tools list (Line 648-652):**
    ```python
    mcp_tools_list.add_argument(
        "--format",
        choices=["json", "yaml", "table"],
        default="table",
        help="Output format",
    )
    ```

22. **MCP tools call (Line 659-663):**
    ```python
    mcp_tools_call.add_argument(
        "--format",
        choices=["json", "yaml", "table"],
        default="json",
        help="Output format",
    )
    ```

23. **MCP tools info (Line 669-673):**
    ```python
    mcp_tools_info.add_argument(
        "--format",
        choices=["json", "yaml", "table"],
        default="table",
        help="Output format",
    )
    ```

24. **MCP validate (Line 679-683):**
    ```python
    mcp_validate.add_argument(
        "--format",
        choices=["json", "yaml", "table"],
        default="table",
        help="Output format",
    )
    ```

**TOTAL: 24 local duplications of global --format argument**

### 3. --force Argument Locations

#### No Global --force (Should be added as semi-global)

#### Local Locations:
1. **Machines return (Line 73-75):**
   ```python
   machines_return.add_argument(
       "--force", action="store_true", help="Force return without confirmation"
   )
   ```

2. **Requests cancel (Line 103):**
   ```python
   requests_cancel.add_argument("--force", action="store_true", help="Force cancellation")
   ```

3. **Templates delete (Line 260-262):**
   ```python
   templates_delete.add_argument(
       "--force", action="store_true", help="Force deletion without confirmation"
   )
   ```

4. **Templates refresh (Line 268):**
   ```python
   templates_refresh.add_argument("--force", action="store_true", help="Force complete refresh")
   ```

5. **Templates generate (Line 325-327):**
   ```python
   templates_generate.add_argument(
       "--force", action="store_true", help="Overwrite existing template files without prompting"
   )
   ```

6. **Init command (Line 700):**
   ```python
   init_parser.add_argument("--force", action="store_true", help="Force overwrite existing config")
   ```

**TOTAL: 6 --force arguments (should be semi-global)**

### 4. --scheduler Argument Locations

#### Global (Line 382-386):
```python
parser.add_argument(
    "--scheduler",
    choices=["default", "hostfactory", "hf"],
    help="Override scheduler strategy for this command",
)
```

#### Local Duplications:
1. **Init command (Line 701-703):**
   ```python
   init_parser.add_argument(
       "--scheduler", choices=["default", "hostfactory"], help="Scheduler type"
   )
   ```

2. **Scheduler show (Line 631):**
   ```python
   scheduler_show.add_argument("--scheduler", help="Show specific scheduler strategy details")
   ```

3. **Scheduler validate (Line 639):**
   ```python
   scheduler_validate.add_argument("--scheduler", help="Validate specific scheduler strategy")
   ```

**TOTAL: 3 local duplications of global --scheduler argument**

### 5. --dry-run Argument Locations

#### Global (Line 377-381):
```python
parser.add_argument(
    "--dry-run",
    action="store_true",
    help="Show what would be done without executing",
)
```

#### Local Duplications:
**NONE FOUND** - --dry-run is only global ✅

### 6. Semi-Global Arguments (Should be available on relevant commands)

#### --all-providers Locations:
1. **Infrastructure discover (Line 150):**
   ```python
   infra_discover.add_argument("--all-providers", action="store_true", help="Discover for all providers")
   ```

2. **Infrastructure show (Line 159):**
   ```python
   infra_show.add_argument("--all-providers", action="store_true", help="Show all providers")
   ```

3. **Templates generate (Line 313-315):**
   ```python
   templates_generate.add_argument(
       "--all-providers", action="store_true", help="Explicitly generate for all active providers"
   )
   ```

**TOTAL: 3 --all-providers arguments (should be semi-global)**

## Summary of Duplications

### Critical Issues:
1. **--provider:** 8 duplications (Global + 8 local = 9 total definitions)
2. **--format:** 24 duplications (Global + 24 local = 25 total definitions)
3. **--scheduler:** 3 duplications (Global + 3 local = 4 total definitions)

### Semi-Global Candidates:
1. **--force:** 6 locations (should be available on destructive operations)
2. **--all-providers:** 3 locations (should be available on multi-provider commands)

### Working Correctly:
1. **--dry-run:** Only global ✅
2. **--verbose:** Only global ✅
3. **--quiet:** Only global ✅
4. **--config:** Only global ✅
5. **--log-level:** Only global ✅
6. **--output:** Only global ✅

## Maintenance Impact

### Current Issues:
- **49 duplicate argument definitions** across the codebase
- **Inconsistent availability** - some commands have --provider, others don't
- **Maintenance burden** - adding new common args requires 20+ file changes
- **Argument conflicts** - global vs local --provider creates confusion

### Benefits of Consolidation:
- **Single source of truth** for common arguments
- **Consistent UX** - all commands have same arguments available
- **Easier maintenance** - add new common args in one place
- **Cleaner code** - remove 49 duplicate definitions

## Implementation Priority

### Phase 1 (High Priority):
1. Remove 24 --format duplications
2. Remove 8 --provider duplications  
3. Remove 3 --scheduler duplications

### Phase 2 (Medium Priority):
1. Make --force semi-global (6 commands)
2. Make --all-providers semi-global (3 commands)

### Phase 3 (Low Priority):
1. Audit other potential common arguments
2. Create argument group functions for future use