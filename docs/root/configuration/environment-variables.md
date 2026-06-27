# Environment Variables Reference

Open Resource Broker reads environment variables at startup to override configuration file values. This page documents every variable that is actually implemented.

## Variable naming convention

- Core settings: `ORB_<SETTING_NAME>`
- Provider settings: `ORB_<PROVIDER>_<SETTING_NAME>`

## Precedence order

1. Environment variables (highest precedence)
2. Configuration file (`config/config.json`)
3. Default values from `default_config.json` (lowest precedence)

## Directory variables

These variables are read by `platform_dirs.py` at ORB initialisation time — before the config loader runs — to locate the working directories. They are **not** processed by the config loader itself.

```bash
# Base directory for all ORB subdirectories.
# Subdirectory overrides below take precedence over ORB_ROOT_DIR for their
# respective directory only.
ORB_ROOT_DIR=/opt/orb

# Override individual directories (each is independent of ORB_ROOT_DIR)
ORB_CONFIG_DIR=/opt/orb/config
ORB_WORK_DIR=/opt/orb/work
ORB_LOG_DIR=/opt/orb/logs
ORB_SCRIPTS_DIR=/opt/orb/scripts
ORB_HEALTH_DIR=/opt/orb/health   # defaults to <work>/health
ORB_CACHE_DIR=/opt/orb/.cache    # defaults to <work>/.cache
```

When `ORB_CONFIG_DIR` is set and `ORB_ROOT_DIR` is not, the root is inferred as the parent of `ORB_CONFIG_DIR`.

Directory values resolved at `orb init` time are persisted to `config.json` so subsequent invocations use the same paths without requiring the environment variables to remain set.

## Config loader variables

These variables are read by `ConfigurationLoader._load_from_env()` and override the corresponding config file keys.

### Core application

```bash
ORB_ENVIRONMENT=production        # maps to config key: environment
ORB_DEBUG=false                   # maps to config key: debug (true/false)
ORB_LOG_LEVEL=INFO                # maps to config key: logging.level
ORB_LOG_CONSOLE_ENABLED=true      # maps to config key: logging.console_enabled
ORB_REQUEST_TIMEOUT=300           # maps to config key: request.default_timeout (seconds)
ORB_MAX_MACHINES_PER_REQUEST=100  # maps to config key: request.max_machines_per_request
ORB_CONFIG_FILE=/path/to/config   # maps to config key: config_file
```

## AWS provider variables

These are read from the environment and mapped into the provider configuration section. Set them in your shell or pass them via Docker `--env` / `--env-file`.

### Authentication and region

```bash
ORB_AWS_REGION=us-west-2
ORB_AWS_PROFILE=production

# IAM role assumption
ORB_AWS_ROLE_ARN=arn:aws:iam::123456789012:role/OrbitRole
ORB_AWS_EXTERNAL_ID=unique-external-id

# Direct credentials (not recommended for production — prefer IAM roles)
ORB_AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
ORB_AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
ORB_AWS_SESSION_TOKEN=temporary-session-token
```

### Service endpoints and retries

```bash
ORB_AWS_ENDPOINT_URL=https://ec2.us-west-2.amazonaws.com
ORB_AWS_STS_ENDPOINT_URL=https://sts.us-west-2.amazonaws.com

ORB_AWS_MAX_RETRIES=3
ORB_AWS_RETRY_MODE=adaptive

ORB_AWS_CONNECT_TIMEOUT=60
ORB_AWS_READ_TIMEOUT=300
```

### Infrastructure defaults

```bash
ORB_AWS_SUBNET_IDS='["subnet-12345", "subnet-67890"]'
ORB_AWS_SECURITY_GROUP_IDS='["sg-abcdef"]'
ORB_AWS_KEY_NAME=my-keypair
ORB_AWS_INSTANCE_TYPE=t3.medium
ORB_AWS_IMAGE_ID=ami-0abcdef1234567890
```

### Failure behaviour

```bash
# Controls what happens when a launch template version update fails.
# Values: fail (default), warn
# warn — logs a warning and falls back to the existing template version.
ORB_AWS_LAUNCH_TEMPLATE__ON_UPDATE_FAILURE=fail

# Controls what happens when resource tagging fails.
# Values: warn (default), fail
# warn — logs a warning and provisioning continues; resources are created without orb: tags.
ORB_AWS_TAGGING__ON_TAG_FAILURE=warn
```

## Embedded UI environment variables

These variables are read when ORB runs with the optional Reflex-based web UI
(`pip install "orb-py[ui]"`).  They are not processed by the core config loader
but are consumed by the UI startup code and `rxconfig.py`.

```bash
# Set to "embedded" when ORB acts as the backend for the Reflex UI.
# Signals to startup scripts that the Reflex backend and ORB API share a port.
ORB_MODE=embedded

# Port for the Reflex backend process (also the ORB API port in embedded mode).
# Default: 8001
ORB_UI_BACKEND_PORT=8001

# Port for the Reflex frontend dev/build server (development mode only;
# not used when serving pre-built static assets in production).
# Default: 3000
ORB_UI_FRONTEND_PORT=3000

# Override the scheduler type at runtime without editing config.json.
# Useful in ephemeral container environments where config files are baked in.
# Values: hostfactory | default | <custom>
ORB_SCHEDULER_OVERRIDE=hostfactory
```

`ORB_WORK_DIR` and `ORB_LOG_DIR` are documented under
[Directory variables](#directory-variables) above.

For the corresponding production deployment steps, see
[Embedded UI deployment](../deployment/embedded-ui.md).

## HostFactory integration variables

When running under IBM Spectrum LSF HostFactory, these variables are set by the scheduler and read by ORB's HostFactory adapter. They are not processed by the core config loader.

```bash
HF_PROVIDER_CONFDIR=/opt/symphony/hostfactory/conf
HF_PROVIDER_WORKDIR=/opt/symphony/hostfactory/work
HF_PROVIDER_LOGDIR=/opt/symphony/hostfactory/logs
HF_LOGGING_CONSOLE_ENABLED=false
HF_LOGLEVEL=INFO
HF_PROVIDER_ACTION_TIMEOUT=600
```

## Type conversion

The config loader converts string values automatically:

| Type | Example |
|------|---------|
| Boolean | `true`, `false` |
| Integer | `300` |
| Float | `0.5` |
| JSON array | `'["a", "b"]'` |
| JSON object | `'{"key": "value"}'` |
| String | anything else |

## Security best practices

Use IAM roles or AWS profiles rather than static credentials:

```bash
# Preferred: IAM role assumption
export ORB_AWS_ROLE_ARN=arn:aws:iam::123456789012:role/OrbitRole

# Preferred: named profile
export ORB_AWS_PROFILE=production
```

Avoid committing `ORB_AWS_ACCESS_KEY_ID` / `ORB_AWS_SECRET_ACCESS_KEY` to source control. Use AWS Systems Manager Parameter Store, HashiCorp Vault, Kubernetes secrets, or Docker secrets for credential storage.

## Troubleshooting

```bash
# List all ORB variables currently set
env | grep ORB_

# Verify AWS configuration
orb providers health aws
```

**Invalid JSON format:**
```bash
# Wrong — missing outer quotes
export ORB_AWS_SUBNET_IDS=["subnet-123"]

# Correct
export ORB_AWS_SUBNET_IDS='["subnet-123"]'
```

**Boolean format:**
```bash
# Wrong — Python-style capitalisation
export ORB_DEBUG=True

# Correct
export ORB_DEBUG=true
```
