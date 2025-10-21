# OnAWS System Tests Documentation

## Overview

The onaws system tests provide end-to-end testing for AWS provider functionality in the Host Factory Plugin. These tests validate the complete lifecycle of EC2 instance provisioning, monitoring, and deallocation through the plugin's API.

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

### Configuration Override System

Scenarios can override default values:
```python
{
    "test_name": "EC2FleetRequest",
    "capacity_to_request": 2,
    "awsprov_base_template": "awsprov_templates2.base.json",
    "overrides": {
        "fleetType": "request",
        "region": "us-west-2",
        "imageId": "ami-custom123"
    }
}
```

## Key Validation Points

### API Response Validation
- **Templates**: Structure, required fields, attribute arrays
- **Requests**: UUID format, success messages
- **Status**: Machine states, IP addresses, timestamps

### AWS Resource Validation
- **Instance State**: pending → running progression
- **Configuration Match**: Template specs vs actual AWS attributes
- **Cleanup**: Proper termination of all resources

### Error Handling
- Timeout protection (120s for provisioning)
- JSON parsing error recovery
- AWS API error propagation
- Resource cleanup on test failure

## File Structure

```
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
└── run_templates/             # Generated test configs
    └── test_sample[ScenarioName]/
        ├── awsprov_templates.json
        ├── config.json
        └── default_config.json
```

## Running Tests

### Prerequisites
- AWS credentials configured
- Required environment variables set
- pytest with aws markers

### Execution
```bash
# Run all onaws tests
pytest tests/onaws/ -m aws

# Run specific scenario
pytest tests/onaws/test_onaws.py::test_sample[EC2FleetRequest] -m aws

# Run with verbose output
pytest tests/onaws/ -m aws -v -s
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
- **Template errors**: Check placeholder substitution in generated configs
- **AWS permissions**: Verify IAM roles and policies
- **Resource limits**: Check AWS service quotas

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

The onaws system tests provide comprehensive validation of AWS provider functionality while maintaining test isolation and reproducibility through dynamic configuration generation.

