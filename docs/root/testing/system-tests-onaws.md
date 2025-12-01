# OnAWS System Tests Documentation

## Overview

The onaws system tests provide end-to-end testing for AWS provider functionality in the Host Factory Plugin. These tests validate the complete lifecycle of EC2 instance provisioning, monitoring, and deallocation through the plugin's API.

IMPORTANT: These test will create resources in your AWS account. In case of a failure or manual termination some resources might persist and will require manual termination.

There are 2 primary sets of tests.
- test_onaws.py - runs broad sweep across various configuration options defined in scenarios.py. Testing different APIs, purchasing models, flags, etc. It goes through full cycle of requesting capacity and releasing it. However, otherwise, tests are quite simple.
- custom tests - cover more complex scenarios, with multiple APIs.


## Architecture

### Core Components

1. **Test Framework** (`test_onaws.py`)
   - Main test orchestrator using pytest
   - Validates API responses against JSON schemas
   - Performs full lifecycle testing (provision → monitor → deallocate)

2. **Template Processor** (`template_processor.py`)
   - Generates test-specific configuration from base templates
   - Handles placeholder substitution with actual AWS values
   - Manages test isolation through separate config directories

3. **Host Factory Mock** (`hfmock.py`)
   - Wrapper around actual AWS provider scripts
   - Provides programmatic interface to shell scripts
   - Handles JSON parsing and error management

4. **Test Scenarios** (`scenarios.py`)
   - Defines parameterized test cases
   - Configures different AWS service types (EC2Fleet, SpotFleet, ASG)
   - Specifies capacity requirements and overrides

5. **Schema Validation** (`plugin_io_schemas.py`)
   - JSON schemas for API response validation
   - Ensures consistent data structures across operations

## Test Flow

### 1. Test Setup
```
setup_host_factory_mock_with_scenario()
├── Extract scenario from test name (e.g., "EC2FleetRequest")
├── Load base templates and apply overrides
├── Generate populated config files in test-specific directory
└── Initialize HostFactoryMock with test configs
```

### 2. Template Generation Process
```
TemplateProcessor.generate_test_templates()
├── Load config from main config/ directory
├── Apply scenario-specific overrides
├── Replace placeholders ({{region}}, {{imageId}}, etc.)
└── Output: awsprov_templates.json, config.json, default_config.json
```

### 3. Main Test Execution (`provide_release_control_loop`)

#### Phase 1: Request Capacity
- Call `request_machines(template_id, capacity)`
- Validate response against `expected_request_machines_schema`
- Extract `requestId` for status tracking

#### Phase 2: Monitor Provisioning
- Poll `get_request_status(requestId)` until complete
- Validate response against `expected_request_status_schema`
- Verify EC2 instances exist and are in correct state
- Timeout after 120 seconds if not complete

#### Phase 3: Validate Instance Attributes
- Select random instance from provisioned set
- Validate against template specifications:
  - Root device volume size
  - Volume type (gp2, gp3, etc.)
  - Subnet ID placement
- Compare AWS API data with template requirements

#### Phase 4: Deallocate Resources
- Call `request_return_machines(instance_ids)`
- Monitor termination via `get_return_requests()`
- Verify all instances reach "shutting-down" or "terminated" state

## Test Scenarios

### Current Active Scenarios

| Scenario | Fleet Type | Base Template | Capacity |
|----------|------------|---------------|----------|
| EC2FleetRequest | request | awsprov_templates2.base.json | 2 |
| EC2FleetInstant | instant | awsprov_templates1.base.json | 2 |

# Custom Multi-Resource Termination Tests

These tests cover specific use cases.

| Test File | Resource Types | Purpose |
|-----------|----------------|---------|
| `test_multi_asg_termination.py` | 2x ASG | Multi-ASG termination + ASG deletion validation |
| `test_multi_spot_fleet_termination.py` | 2x SpotFleet | Multi-SpotFleet termination + request cancellation |
| `test_multi_ec2_fleet_termination.py` | 2x EC2Fleet | Multi-EC2Fleet termination + maintain fleet deletion |
| `test_multi_resource_termination.py` | ASG + EC2Fleet + SpotFleet + RunInstances | Cross-resource termination validation |

These tests validate:
- Resource grouping logic across multiple instances of same type
- Mixed resource type termination in single operation
- Resource-specific cleanup (ASG deletion, fleet cancellation)
- Performance optimization through resource mapping



## File Structure
```bash
tests/onaws/
├── test_onaws.py              # Main test file
├── scenarios.py               # Test case definitions
├── template_processor.py      # Config generation
├── plugin_io_schemas.py       # JSON validation schemas
├── config_templates/          # Base template files
│   ├── awsprov_templates.base.json
│   ├── awsprov_templates1.base.json
│   ├── awsprov_templates2.base.json
│   ├── config.base.json
│   └── default_config.base.json
├── test_multi_asg_termination.py          # Multi-ASG termination tests
├── test_multi_spot_fleet_termination.py   # Multi-SpotFleet termination tests
├── test_multi_ec2_fleet_termination.py    # Multi-EC2Fleet termination tests
├── test_multi_resource_termination.py     # Cross-resource termination tests
└── run_templates/             # Generated test configs
    └── full_cycle_test[ScenarioName]/
        ├── awsprov_templates.json
        ├── config.json
        └── default_config.json
```

## Running Tests

### Prerequisites
- AWS credentials configured
- Required environment variables set
- pytest with aws markers

### Execution Parameter Sweep
```bash
make system-tests

# Run all tests, stop on the first failure
python -m pytest tests/onaws/test_onaws.py -v -x

#Run a specific parameterized configuration of a test
pytest "tests/onaws/test_onaws.py::full_cycle_test[hostfactory.SpotFleetRequest.ABIS]" -v -x

#Execute all system tests in parallel
uv run python -m pytest tests/onaws/test_onaws.py -v -m manual_aws --no-cov --tb=long -n 10


# Run specific scenario
pytest tests/onaws/test_onaws.py::full_cycle_test[EC2FleetRequest] -m aws


```


### Execution Custom Tests
```bash
pytest tests/onaws/test_multi_asg_termination.py -v
pytest tests/onaws/test_multi_spot_fleet_termination.py -v
pytest tests/onaws/test_multi_ec2_fleet_termination.py -v
pytest tests/onaws/test_multi_resource_termination.py -v
or
pytest tests/onaws/test_multi_*.py -n 4 -v
```

### Environment Variables
```bash
AWS_REGION=us-east-1
HF_PROVIDER_CONFDIR=./tests/onaws/run_templates/test_name
HF_LOGDIR=./logs
LOG_DESTINATION=file
```

## Monitoring and Debugging

### Logging
- Test logs: `logs/awsome_test.log`
- Provider logs: `AWS_PROVIDER_LOG_DIR`
- Mock logs: `hfmock.log`

### Common Issues
- **Timeout**: Increase `MAX_TIME_WAIT_FOR_CAPACITY_PROVISIONING_SEC`

## Extension Points

### Adding New Scenarios
1. Add entry to `scenarios.py`
2. Create base template if needed
3. Define required overrides
4. Test with single scenario first

### Custom Validations
1. Add validation function to `test_onaws.py`
2. Include in `validate_instance_attributes()`
3. Update schemas if new fields required

