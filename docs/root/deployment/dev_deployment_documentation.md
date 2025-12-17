# ORB Provider Deployment Documentation

This document describes how to configure the Open Host Factory Plugin (ORB) as a provider in IBM Spectrum Symphony Host Factory.

## Installation of the Open Host Factory Plugin

### Prerequisites
- Python 3.9+
- Git
- Virtual environment support

### Installation Steps

1. **Navigate to the provider plugins directory:**
```bash
cd /opt/ibm/spectrumcomputing/hostfactory/1.2/providerplugins
```

2. **Clone the repository:**
```bash
mkdir -p orb
git clone https://github.com/awslabs/open-hostfactory-plugin.git ./orb
cd orb
```

3. **Set up Python virtual environment:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

4. **Install dependencies:**

**Option A: Fast installation with uv (recommended):**
```bash
pip install uv
make dev-install-uv
```

**Option B: Traditional pip installation:**
```bash
make dev-install-pip
# Or manually:
pip install -e ".[dev]"
```

5. **Verify installation:**
```bash
orb --version
orb --help
```






## Configuration of ORB

There are 2 ways to configure ORB:

1. **Define a new provider altogether** (this document)
2. **Define a new plugin for the existing provider**


## Step 1: Define New Provider

### 1.1 Create Provider Directory

Navigate to the providers folder and create a folder for the new provider.
```bash
cd /opt/ibm/spectrumcomputing/hostfactory/conf/providers
mkdir aws_orb_provider
```

Note: Due to current configuration, actual config files need to be placed into work directory instead of plugin config dir.

Copy these 3 configuration files from the ORB repository:

- `default_config.json`
  - Base configuration containing defaults for ORB. Does not need to be changed


- `config.json`
  - Change according to your setup. See example in the appendix. Needs to use existing key pair.

- `awsprov_templates.json`
  - Templates configuration for AWS resources. Needs to be updated

**Base Configuration:**
```bash
cp /opt/ibm/spectrumcomputing/hostfactory/1.2/providerplugins/orb/config/default_config.json opt/ibm/spectrumcomputing/hostfactory/work/config/
cp /opt/ibm/spectrumcomputing/hostfactory/1.2/providerplugins/orb/config/config.json opt/ibm/spectrumcomputing/hostfactory/work/config/
cp /opt/ibm/spectrumcomputing/hostfactory/1.2/providerplugins/orb/config/awsprov_templates.json opt/ibm/spectrumcomputing/hostfactory/work/config/
```


### 1.2 Register Provider in Host Factory

Edit the provider configuration file:
```bash
vi /opt/ibm/spectrumcomputing/hostfactory/conf/providers/hostProviders.json
```

Add the new provider configuration:
```json
{
    "name": "aws_orb_provider",
    "enabled": 1,
    "plugin": "orb",
    "confPath": "${HF_CONFDIR}/providers/aws_orb_provider/",
    "workPath": "${HF_WORKDIR}/providers/aws_orb_provider/",
    "logPath": "${HF_LOGDIR}/"
}
```


## Step 2: Configure Provider Plugin

Edit the provider plugins configuration:
```bash
vi /opt/ibm/spectrumcomputing/hostfactory/conf/providerplugins/hostProviderPlugins.json
```

Add the ORB plugin configuration and disable other plugins:
```json
{
    "name": "orb",
    "enabled": 1,
    "scriptPath": "${HF_TOP}/${HF_VERSION}/providerplugins/orb/scripts/"
}
```

## Step 3: Configure Requestor

Configure the requestor to recognize the new provider:
```bash
vi /opt/ibm/spectrumcomputing/hostfactory/conf/requestors/hostRequestors.json
```

Update the requestor configuration:
```json
{
    "version": 2,
    "requestors": [
        {
            "name": "symAinst",
            "enabled": 1,
            "plugin": "symA",
            "confPath": "${HF_CONFDIR}/requestors/symAinst/",
            "workPath": "${HF_WORKDIR}/requestors/symAinst/",
            "logPath": "${HF_LOGDIR}/",
            "providers": ["aws_orb_provider"],
            "requestMode": "POLL"
        },
        {
            "name": "admin",
            "enabled": 1,
            "providers": ["aws_orb_provider"],
            "requestMode": "REST_MANUAL"
        }
    ]
}
```

## Set Environment Variables in invoke_provider.sh

```
/opt/ibm/spectrumcomputing/hostfactory/1.2/providerplugins/orb/scripts/invoke_provider.sh
export USE_LOCAL_DEV="true"         # Set true for this type of deployment
export LOG_CONSOLE_ENABLED=false    # STDOUT will interfere with HF expected output.
export LOG_SCRIPTS="true"           # For debug purposes log raw IO between HF and the plugin
export LOG_LEVEL=DEBUG              # Enable for plugin logging
```


## Directory Structure

After configuration, your directory structure should look like:


hostfactory/work/providers/aws_orb_provider/dataopt/ibm/spectrumcomputing/hostfactory/work/providers/aws_orb_provider/data/
```
hostfactory
├── conf/
│   ├── providers/
│   │   ├── awsinst/                    # Original AWS provider (disabled)
│   │   ├── aws_orb_provider/          # New ORB provider
│   │   │   ├── <...>                   # Currently no config files here!
│   │   └── hostProviders.json          # Provider registry (update)
│   ├── providerplugins/
│   │   └── hostProviderPlugins.json    # Plugin registry (update)
│   └── requestors/
│       └── hostRequestors.json         # Requestor configuration (update)
├── work/
│   └── config/                         # config.json, default-config.json, awsprov_templates.json
│   └── logs/
│       └── app.log                     # ORB Plugin Logs
│   └── providers/
│       └── aws_orb_provider/
│           └── data/
├               ├──request_database.json. # Request/machine data
├── log/
│   ├── hostfactory.log                 # Host Factory logs
│   └── scripts.log                     # Logs from Host Factory scripts invocation for debug.
│   └── symAinst.log                    # Requestors logs
└── 1.2/
    └── providerplugins/
        └── orb/
            ├── .venv/
            ├── src/                    # ORB source code
            └── scripts/                # Provider scripts
                ├── getAvailableTemplates.sh
                ├── requestMachines.sh
                ├── getRequestStatus.sh
                └── requestReturnMachines.sh
                └── invoke_provider.sh         # Edit this file


```


### Log Locations

Check these log files for troubleshooting:
- **Host Factory logs:** `/opt/ibm/spectrumcomputing/hostfactory/log/hostfactory.log`
- **ORB application logs:** `/opt/ibm/spectrumcomputing/hostfactory/log/app.log`
- **Provider work directory:** `/opt/ibm/spectrumcomputing/hostfactory/work/providers/aws_orb_provider/`

## Execution

To apply any configuration changes you need to restart HostFactory

```bash
egosh service stop HostFactory
sleep 2
egosh service start HostFactory
```

To have a clean run, you can remove all the previous state associated with HF and requestor plugin (adjust to your paths):

```bash
rm /opt/ibm/spectrumcomputing/hostfactory/work/*.json -f
rm /opt/ibm/spectrumcomputing/hostfactory/log/* -f
rm /opt/ibm/spectrumcomputing/hostfactory/work/logs/app.log -f
rm /opt/ibm/spectrumcomputing/hostfactory/work/requestors/symAinst/* -f
rm /opt/ibm/spectrumcomputing/hostfactory/db/hf.db -f
rm /opt/ibm/spectrumcomputing/hostfactory/1.2/providerplugins/orb/awscpinst/data/*.json -f
```

Note: symAinst-requestor.log is visible only if plugin successfully started and returned list of available templates.

# Appendix


### Sample config.json
```json
{
  "version": "2.0.0",
  "provider": {
    "active_provider": "aws-default",
    "providers": [
      {
        "name": "aws-default",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1",
          "profile": "default",
          "max_retries": 3,
          "timeout": 30,
          "ssm_parameter_prefix": "/hostfactory/templates/"
        }
      }
    ],
    "selection_policy": "FIRST_AVAILABLE",
    "provider_defaults": {
      "aws": {
        "template_defaults": {
          "image_id": "ami-XXXXXXXXXXXXXXXXX",
          "instance_type": "t2.micro",
          "security_group_ids": [
            "sg-XXXXXXXXX"
          ],
          "subnet_ids": [
            "subnet-XXXXXXXXX"
          ],
          "key_name": "my-key-pair",
          "provider_api": "EC2Fleet",
          "price_type": "ondemand",
          "tags": {
            "Environment": "development",
            "Project": "hostfactory"
          }
        },
        "extensions": {
          "ami_resolution": {
            "enabled": true
          }
        }
      }
    }
  },
  "scheduler": {
    "type": "hostfactory",
    "config_root": "$HF_PROVIDER_CONFDIR"
  },
  "logging": {
    "level": "INFO",
    "file_path": "logs/app.log",
    "console_enabled": true
  },
  "storage": {
    "strategy": "json",
    "default_storage_path": "data",
    "json_strategy": {
      "storage_type": "single_file",
      "base_path": "data",
      "filenames": {
        "single_file": "request_database.json",
        "split_files": {
          "templates": "templates.json",
          "requests": "requests.json",
          "machines": "machines.json"
        }
      }
    }
  }

}
```
