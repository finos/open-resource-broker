# ORB Provider Deployment Documentation

This document describes how to configure the Open Resource Broker (ORB) as a provider in IBM Spectrum Symphony HostFactory.

## Installation of the Open Resource Broker

### Prerequisites
- Python 3.10+
- Git
- Virtual environment support

### Installation Steps
<details>
<summary>Development Install from Repository</summary>

Note: initial installation paths of IBM Symphony and HostFactory along with the exact versions may differ.

The examples below use two path variables. Set them once and reuse them:

- `EGO_TOP=/opt/ibm/spectrumcomputing`: Symphony install root.
- `HF_TOP=$EGO_TOP/hostfactory`: HostFactory subtree. This matches the value `conf/profile.hf` exports at runtime, so do not double-prefix it with another `hostfactory/`.

```bash
# Set path variables
export EGO_TOP=/opt/ibm/spectrumcomputing
export HF_TOP=$EGO_TOP/hostfactory

# Clone the repository under the plugin directory
cd ${HF_TOP}/1.2/providerplugins
mkdir -p orb
git clone https://github.com/finos/open-resource-broker.git ./orb
cd orb

# Create a venv and install in editable mode
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Verify the install
orb --version
```

</details>

## Debug Logging

HostFactory logging splits into two layers: the HostFactory core daemon and the ORB provider plugin. Turn both on during first-time bring-up; turn them off once the deployment is stable.

<details>
<summary>More</summary>

### HostFactory core

The HostFactory daemon writes its own log independently of any provider plugin. To raise verbosity, edit the HostFactory configuration file:

```bash
vi ${HF_TOP}/conf/hostfactoryconf.json
```

Set:

```json
"HF_LOGLEVEL": "LOG_DEBUG"
```

### ORB provider plugin

Set the ORB plugin log level. The path depends on the install layout. For the canonical dev layout the file lives under the plugin's own `config/` directory:

```bash
vi ${HF_TOP}/1.2/providerplugins/orb/config/config.json
```

```json
"logging": {
  "level": "DEBUG",
  "file_path": "logs/app.log",
  "console_enabled": false
}
```

For where each log lives, see [Log Locations](#log-locations) below.

### Speed up host return for debugging

Set `host_return_policy: immediate` for debugging the requestor plugin, then restart HostFactory. The default `lazy` returns hosts only at the configurable billing boundary (~60 minutes by default). `immediate` returns any idle host within ~1 minute, giving fast feedback during debugging and testing. Revert to `lazy` before production.

```bash
vi ${HF_TOP}/conf/requestors/symAinst/symAinstreq_config.json
```

```json
"host_return_policy": "immediate"
```

</details>

## Configuration of HostFactory to use ORB as a new provider plugin

### Step 1: Register ORB provider with HostFactory

Edit the provider configuration file:

```bash
vi ${HF_TOP}/conf/providers/hostProviders.json
```

Add the ORB provider entry:

```json
{
  "version": 2,
  "providers": [
    {
      "name": "orb",
      "enabled": 1,
      "plugin": "orb",
      "confPath": "${HF_CONFDIR}/providers/orb/",
      "workPath": "${HF_WORKDIR}/providers/orb/",
      "logPath": "${HF_LOGDIR}/"
    }
  ]
}
```

To disable other providers in the same file, set their `"enabled"` field to `0`.

### Step 2: Register ORB provider plugin with HostFactory

Edit the provider plugins configuration:

```bash
vi ${HF_TOP}/conf/providerplugins/hostProviderPlugins.json
```

Add the ORB plugin entry:

```json
{
  "version": 2,
  "providerplugins": [
    {
      "name": "orb",
      "enabled": 1,
      "scriptPath": "${HF_TOP}/${HF_VERSION}/providerplugins/orb/scripts/"
    }
  ]
}
```

To disable other plugins in the same file, set their `"enabled"` field to `0`.

### Step 3: Create the provider directory

Create an empty directory for the ORB provider. HostFactory checks that this path exists, but the actual ORB configuration is kept under the plugin install root, not here.

```bash
mkdir -p ${HF_TOP}/conf/providers/orb
```

### Step 4: Configure the requestor

Configure the requestor to recognise the new provider:

```bash
vi ${HF_TOP}/conf/requestors/hostRequestors.json
```

Set `"providers": ["orb"]` on every requestor entry that should route demand through ORB:

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
      "providers": ["orb"],
      "requestMode": "POLL"
    },
    {
      "name": "admin",
      "enabled": 1,
      "providers": ["orb"],
      "requestMode": "REST_MANUAL"
    }
  ]
}
```

### Step 5: Set HostFactory environment variables for ORB

HostFactory does not pick up variables from `.bashrc`. It has its own environment file (`profile.hf`) that is sourced by every HF-spawned process: the HostFactory daemon, the requestor, and every provider plugin call. Anything exported here is global to the HF runtime.

Two ORB variables to set:

- `USE_LOCAL_DEV=true`: run ORB from the source tree. `invoke_provider.sh` execs `python src/orb/run.py` instead of the installed `orb` console-script, so edits under `src/orb/` take effect on the next HF call without reinstall.
- `LOG_SCRIPTS=true`: record full HF/plugin I/O into `${HF_LOGDIR}/scripts.log` (one block per call). Useful during bring-up; turn off in production to keep the log small.

Append both to `profile.hf`:

```bash
vi ${HF_TOP}/conf/profile.hf
```

```bash
export USE_LOCAL_DEV=true
export LOG_SCRIPTS=true
```

### Step 6: Configure ORB

This step writes ORB defaults for the current AWS account and copies the entry scripts into the `orb/scripts/` directory referenced by `hostProviderPlugins.json`.

```bash
orb init
orb templates generate
```

After this, `${HF_TOP}/1.2/providerplugins/orb/scripts/` contains the five HF entry scripts plus `invoke_provider.sh`, and `${HF_TOP}/1.2/providerplugins/orb/awscpinst/config/awsprov_templates.json` contains the generated AWS templates.

## Directory Structure

After configuration, the relevant subtree under `${HF_TOP}` looks like:

```
hostfactory/
├── conf/
│   ├── profile.hf                          # HF environment (USE_LOCAL_DEV, LOG_SCRIPTS go here)
│   ├── hostfactoryconf.json                # HF core config (HF_LOGLEVEL, etc.)
│   ├── providers/
│   │   ├── awsinst/                        # Original AWS provider (disable)
│   │   ├── orb/                            # ORB provider (empty, checked for existence only)
│   │   └── hostProviders.json              # Provider registry (update)
│   ├── providerplugins/
│   │   └── hostProviderPlugins.json        # Plugin registry (update)
│   └── requestors/
│       ├── hostRequestors.json             # Requestor configuration (update)
│       └── symAinst/
│           └── symAinstreq_config.json     # host_return_policy lives here
├── work/
│   └── providers/
│       └── orb/
│           └── data/
│               └── request_database.json   # Request/machine state
├── log/
│   ├── hostfactory.<hostname>.log          # HostFactory core log
│   ├── scripts.log                         # Wire-format I/O between HF and plugin (LOG_SCRIPTS)
│   └── symAinst-requestor.<hostname>.log   # Requestor log
├── db/
│   └── hf.db                               # HF state database
└── 1.2/
    └── providerplugins/
        └── orb/                            # Plugin install root (this repo, checked out here)
            ├── .venv/
            ├── src/orb/                    # ORB source (used when USE_LOCAL_DEV=true)
            ├── config/config.json          # ORB plugin config (logging level, etc.)
            ├── awscpinst/config/
            │   └── awsprov_templates.json  # Generated by `orb templates generate`
            ├── logs/
            │   └── orb.log                 # Structured ORB log
            └── scripts/                    # HF entry scripts (referenced by hostProviderPlugins.json)
                ├── getAvailableTemplates.sh
                ├── getRequestStatus.sh
                ├── getReturnRequests.sh
                ├── requestMachines.sh
                ├── requestReturnMachines.sh
                ├── templateWizard.sh       # Interactive helper, not called by HF
                └── invoke_provider.sh      # Wrapper sourced by every entry script
```

### Log Locations

Check these log files for troubleshooting:

- **HostFactory core:** `${HF_TOP}/log/hostfactory.<hostname>.log`.
- **Plugin wire-format log:** `${HF_TOP}/log/scripts.log`. Raw I/O between HostFactory and the plugin, one block per call: the JSON HF sent in, then the JSON the plugin returned. Only populated when `LOG_SCRIPTS=true`.
- **ORB structured log:** `${HF_TOP}/1.2/providerplugins/orb/logs/orb.log`. Per-line JSON log for full ORB tracing.
- **Requestor:** `${HF_TOP}/log/symAinst-requestor.<hostname>.log`.
- **Provider work directory:** `${HF_TOP}/work/providers/orb/`.

## Execution

To apply configuration changes, restart HostFactory:

```bash
egosh service stop HostFactory
sleep 2
egosh service start HostFactory
```

### Verify the plugin is wired up

```bash
# 1. HostFactory is running
egosh service list | grep HostFactory

# 2. ORB CLI itself works against your AWS account
orb templates list

# 3. HF can call into the plugin (templates surface through HF)
tail -F ${HF_TOP}/log/scripts.log
# Within ~30 seconds you should see a `=== Caller: getAvailableTemplates.sh ===`
# block followed by JSON output. If not, recheck Step 1 and Step 2.
```

### Reset state for a clean run

To wipe all previous HF and requestor state (adjust paths if your install differs):

```bash
rm -f ${HF_TOP}/work/*.json
rm -f ${HF_TOP}/log/*
rm -f ${HF_TOP}/work/requestors/symAinst/*
rm -f ${HF_TOP}/db/hf.db
rm -f ${HF_TOP}/work/providers/orb/data/*.json
```

Note: `symAinst-requestor.<hostname>.log` only appears once the plugin has successfully started and returned the list of available templates.

