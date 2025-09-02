# Import Guidelines

This document provides guidelines for importing modules after the value object decomposition and other refactoring activities.

##  Current Import Locations

### Value Objects by Domain

#### Request Domain (`src.domain.request.value_objects`)
```python
from src.domain.request.value_objects import (
    RequestStatus,      # Status of requests (pending, running, completed, etc.)
    RequestType,        # Type of request (acquire, return)
    RequestId,          # Unique request identifier
    MachineReference,   # Reference to a machine in a request
    RequestTimeout,     # Request timeout configuration
    MachineCount,       # Number of machines in request
    RequestTag,         # Tags for requests
    RequestConfiguration, # Request configuration
    LaunchTemplateInfo, # Launch template information
    RequestEvent        # Request events
)
```

#### Machine Domain (`src.domain.machine.value_objects`)
```python
from src.domain.machine.value_objects import (
    MachineStatus,      # Status of individual machines [!] MOVED FROM REQUEST
    MachineId,          # Unique machine identifier
    MachineType,        # Type/category of machine
    PriceType,          # Pricing model (on-demand, spot, etc.)
    MachineConfiguration, # Machine configuration
    MachineEvent,       # Machine events
    HealthCheck,        # Health check configuration
    HealthCheckResult,  # Health check results
    IPAddressRange,     # IP address ranges
    MachineMetadata,    # Machine metadata
    ResourceTag         # Resource tags
)
```

#### Template Domain (`src.domain.template.value_objects`)
```python
from src.domain.template.value_objects import (
    TemplateId,         # Unique template identifier
    ProviderHandlerType # Provider handler type (EC2Fleet, SpotFleet, etc.)
)
```

#### Base Domain (`src.domain.base.value_objects`)
```python
from src.domain.base.value_objects import (
    ResourceId,         # Base resource identifier
    ResourceQuota,      # Resource quota information
    InstanceId,         # AWS instance identifier
    InstanceType,       # AWS instance type
    IPAddress,          # IP address value object
    Tags,               # Generic tags
    ARN,                # AWS ARN
    PriceType,          # Pricing type enum
    AllocationStrategy  # Allocation strategy enum
)
```

### Command Handlers (`src.interface.command_handlers`)

```python
from src.interface.command_handlers import (
    InterfaceCommandHandler,        # Base interface handler [!] RENAMED FROM BaseCommandHandler
    GetAvailableTemplatesCLIHandler,
    GetRequestStatusCLIHandler,
    RequestMachinesCLIHandler,
    GetReturnRequestsCLIHandler,
    RequestReturnMachinesCLIHandler,
    MigrateRepositoryCLIHandler
)
```

## [!] Common Migration Issues

### 1. MachineStatus Location Change
```python
# [[]] OLD (BROKEN):
from src.domain.request.value_objects import MachineStatus

# [[]] NEW (CORRECT):
from src.domain.machine.value_objects import MachineStatus
```

**Reason**: `MachineStatus` represents the state of individual machines, which belongs in the machine domain, not the request domain.

### 2. BaseCommandHandler Rename
```python
# [[]] OLD (BROKEN):
from src.interface.command_handlers import BaseCommandHandler

# [[]] NEW (CORRECT):
from src.interface.command_handlers import InterfaceCommandHandler
```

**Reason**: The base command handler was renamed to better reflect its role as an interface layer handler.

##  Validation Tools

### 1. Import Validation Script
```bash
python scripts/validate_imports.py
```

### 2. Import Validation Tests
```bash
python -m pytest tests/test_import_validation.py -v
```

### 3. Pre-commit Hooks
```bash
pre-commit install
pre-commit run --all-files
```

##  Best Practices

### 1. Domain Boundaries
- Import value objects from their appropriate domain
- Don't import across domain boundaries unless necessary
- Use the orchestrator modules (e.g., `value_objects.py`) rather than specific files

### 2. Import Organization
```python
# Group imports by domain
from src.domain.request.value_objects import RequestStatus, RequestType
from src.domain.machine.value_objects import MachineStatus, MachineId
from src.domain.template.value_objects import TemplateId

# Separate infrastructure imports
from src.infrastructure.logging.logger import get_logger
from src.interface.command_handlers import InterfaceCommandHandler
```

### 3. Avoiding Circular Imports
- Import from higher-level orchestrator modules
- Use TYPE_CHECKING for type hints when needed
- Consider dependency injection instead of direct imports

##  Deprecated Patterns

These import patterns are deprecated and will cause ImportError:

```python
# [[]] DEPRECATED - Will fail
from src.domain.request.value_objects import MachineStatus
from src.interface.command_handlers import BaseCommandHandler

# [[]] AVOID - Direct imports from specialized modules
from src.domain.request.request_types import RequestStatus  # Use orchestrator instead
```

##  Troubleshooting

### ImportError: cannot import name 'X'
1. Check if the import was moved during refactoring
2. Use the validation script: `python scripts/validate_imports.py`
3. Check this documentation for current locations
4. Run the import validation tests

### Module not found errors
1. Ensure you're running from the project root
2. Check that `src/` is in your Python path
3. Verify the module file exists and has appropriate `__init__.py`

##  Related Documentation

- [Domain-Driven Design Architecture](./architecture/system_overview.md)
- [Value Object Decomposition](./developer_guide/data_models.md)
- [Testing Guidelines](./developer_guide/testing.md)
