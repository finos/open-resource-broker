# Troubleshooting Guide

This guide covers common issues and their solutions when using ORB.

## Configuration Issues

### 1. Configuration Not Found

**Error Message:**
```
ERROR: Configuration file not found
  No configuration found in:
    - /current/dir/config/config.json
    - ~/.config/orb/config.json
    - ~/.local/orb/config/config.json

Run 'orb init' to create configuration
```

**Solution:**
```bash
orb init
```

This creates the configuration directory and files needed for ORB to work.

### 2. Templates Not Found

**Error Message:**
```
ERROR: Templates file not found

Searched for:
  - awsprov_templates.json
  - templates.json

In directories:
  - ~/.config/orb/templates/
  - ~/.config/orb/

Run 'orb templates generate' to create example templates
```

**Solution:**
```bash
orb templates generate
```

This creates example templates you can customize for your environment.

### 3. AWS Credentials Missing

**Error Message:**
```
ERROR: AWS credentials not found

Profile: default
Region: us-east-1

Configure AWS credentials:
  aws configure --profile default

Or set environment variables:
  export AWS_ACCESS_KEY_ID=...
  export AWS_SECRET_ACCESS_KEY=...
```

**Solution:**
```bash
# Option 1: Use AWS CLI
aws configure

# Option 2: Use specific profile
aws configure --profile myprofile

# Option 3: Environment variables
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
export AWS_DEFAULT_REGION=us-east-1
```

## Configuration Locations

ORB looks for configuration in different locations depending on how it was installed:

### Development Mode
```
/path/to/project/
├── config/
│   ├── config.json
│   └── templates/
└── work/
```

### Virtual Environment Install
```
/path/to/project/
├── .venv/
├── config/              # Next to .venv
│   ├── config.json
│   └── templates/
└── work/
```

### User Install (pip install --user)
```
~/.local/orb/
├── config/
│   ├── config.json
│   └── templates/
└── work/
```

### System Install
```
/usr/local/orb/          # or /opt/orb/
├── config/
│   ├── config.json
│   └── templates/
└── work/
```

### Platform-Specific Locations

**Linux/Unix:**
- Config: `~/.config/orb/`
- Data: `~/.local/share/orb/`
- Cache: `~/.cache/orb/`

**macOS:**
- Config: `~/Library/Application Support/orb/config/`
- Data: `~/Library/Application Support/orb/work/`
- Cache: `~/Library/Caches/orb/`

**Windows:**
- Config: `%APPDATA%\orb\`
- Data: `%LOCALAPPDATA%\orb\`

## Environment Variables

You can override default locations with environment variables:

```bash
# Override config directory
export ORB_CONFIG_DIR=/custom/path/config

# Override config file directly
export ORB_CONFIG_FILE=/custom/path/config.json

# Legacy HostFactory variables
export HF_PROVIDER_CONFDIR=/path/to/hostfactory/conf
export HF_PROVIDER_WORKDIR=/path/to/hostfactory/work
```

## Validation Commands

### Check Configuration
```bash
orb config show
```

### Validate Setup
```bash
orb validate
```

### List Templates
```bash
orb templates list
```

### Test AWS Connection
```bash
orb providers test aws
```

## Common Solutions

### Reset Configuration
```bash
# Remove existing config
rm -rf ~/.config/orb/

# Reinitialize
orb init
```

### Force Reinitialize
```bash
orb init --force
```

### Use Custom Config Location
```bash
orb --config /path/to/config.json templates list
```

### Debug Mode
```bash
export ORB_LOG_LEVEL=DEBUG
orb templates list
```

## Getting Help

If you're still having issues:

1. Check the logs in your work directory
2. Run with debug logging: `ORB_LOG_LEVEL=DEBUG orb <command>`
3. Validate your setup: `orb validate`
4. Check the documentation: https://awslabs.github.io/open-resource-broker/
5. File an issue: https://github.com/awslabs/open-resource-broker/issues