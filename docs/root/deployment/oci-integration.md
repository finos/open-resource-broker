# OCI Integration Guide

This guide documents OCI provider support in Open Resource Broker and the
recommended operational flow.

For handler fields, machine data, fulfilment, and release behaviour, see the
[OCI Compute Instances provider reference](../providers/oci/compute.md).

## Overview

OCI support includes:

- generated OCI catalogs with template validation, listing, and inspection
- machine acquire and return workflows through OCI compute
- provider routing via `provider_name=oci-default`, `provider_type=oci`,
  `provider_api=OCICompute`

## Installation and initialization

Install ORB from a source checkout (`pip install .`) and install the OCI CLI separately.
The provider uses the `oci` executable on PATH; importing ORB does not require the OCI SDK.
OCI is discovered through the `orb.providers` entry point.

```bash
orb init --non-interactive --provider-type oci --oci-region us-phoenix-1 --oci-credential-source instance_principal
orb templates generate
orb templates list
```

For a workstation, use `--oci-credential-source profile --oci-profile DEFAULT`.
Custom profile names are supported by `--oci-profile`. For Symphony, add
`--scheduler hostfactory` to initialization. With a custom `--config-dir`, pass
`--config /path/to/config.json` to subsequent commands.

### Local browser-session authentication

For an existing OCI CLI session, use its profile name and select `security_token`
explicitly. This passes both `--auth security_token` and `--profile` to OCI CLI:

```bash
oci session authenticate --region us-phoenix-1 --profile-name ORB
orb init --non-interactive --provider-type oci --oci-region us-phoenix-1 --oci-credential-source security_token --oci-profile ORB
```

Use an existing session profile instead of `ORB` if one is already configured.
Session tokens expire; reauthenticate when needed. Provider health and credential
checks require the OCI CLI and make an authenticated `oci os ns get` request.
A failed probe reports unhealthy; a successful probe does not prove capacity or
permission to launch every resource.
See [Oracle's token authentication guide](https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/clitoken.htm).

## Provider configuration

Choose exactly one OCI config baseline:

```bash
# Remote/production: ORB runs on OCI Compute with instance principal auth.
cp config/oci_config.remote.example.json config/config.json

# Local/dev: ORB runs on a workstation with an OCI CLI profile.
cp config/oci_config.local.example.json config/config.json
```

Run these commands from the repository root. Use the standard `config.json`
filename so early CLI discovery and the application load the same provider
configuration. A copied file named `oci_config.json` alone can leave early
discovery using defaults, even when passed with `--config`.

For an existing prepared configuration, copy it into place before running ORB:

```bash
cp config/oci_config.json config/config.json
export ORB_CONFIG_DIR="$PWD/config"
orb --config "$ORB_CONFIG_DIR/config.json" templates list
```

Remote instance-principal config uses `credential_source: instance_principal` and must not set `profile`. Local profile config uses `credential_source: profile` and `profile: DEFAULT`; `DEFAULT` is the standard OCI CLI profile name and can be replaced with another `~/.oci/config` profile.

Remote provider block:

```json
{
  "provider": {
    "providers": [
      {
        "name": "oci-default",
        "type": "oci",
        "enabled": true,
        "config": {
          "region": "us-phoenix-1",
          "credential_source": "instance_principal"
        }
      }
    ]
  }
}
```

OCI templates keep shape and sizing examples in the packaged `src/orb/providers/oci/config/oci_templates.json`. Deployment-specific values such as image OCID, subnet OCID, compartment OCID, NSGs, SSH keys, tags, and user data live in `provider_defaults.oci.template_defaults` in the config file.

`orb templates list` displays resolved templates, so it includes values inherited
from that deployment configuration. These values are not added to the shipped
catalog. The examples contain no default bootstrap script or environment tag;
add your own only when required by your deployment. ARM/HPC image placeholders
are intentionally incomplete and override a shared x86 image. See
[catalog examples and configured templates](../providers/oci/templates.md#catalog-examples-and-configured-templates)
for how to interpret the output and prepare a selected template.

Build release artifacts from a clean checkout containing no local deployment
configuration. Inspect source archives and container inputs before distribution.
Keep deployment configs, private keys, OCI session files, logs, and live-canary
reports outside release inputs. Resolved template output and reports can contain
resource IDs, network details, and bootstrap data; treat them as deployment
records rather than portable examples.

### Broker instance-principal setup

Create a Python virtual environment for the broker using a Python version
supported by ORB. Worker images need the runtime required by their own workload
and bootstrap.

Create a dynamic group matching the broker instance explicitly, for example:

```text
ALL {instance.id = '<broker-instance-ocid>'}
```

For a broker provisioning into one compartment, the tested policy grants are:

```text
Allow dynamic-group <broker-group> to manage instance-family in compartment id <worker-compartment-ocid>
Allow dynamic-group <broker-group> to use volume-family in compartment id <worker-compartment-ocid>
Allow dynamic-group <broker-group> to use virtual-network-family in compartment id <worker-compartment-ocid>
Allow dynamic-group <broker-group> to use compute-clusters in compartment id <worker-compartment-ocid>
```

The compute-cluster grant is needed for RDMA cluster placement. These statements
assume the worker subnet, NSGs, custom image, and cluster are in that compartment;
adjust policy scope when resources are elsewhere. Membership must not include
workers merely because they share the broker's compartment. See
[Oracle's instance-principal guide](https://docs.oracle.com/en-us/iaas/Content/Identity/Tasks/callingservicesfrominstances.htm).

Install ORB and OCI CLI in virtual environments on the broker, configure
`credential_source: instance_principal`, and verify API access there before a live
canary. No OCI profile, user private key, or workstation session token is needed
on the broker. A profile-based workstation canary does not validate this path.
Use the one-machine gate below from the broker itself, then independently inspect
worker termination and boot-volume cleanup. Remove temporary validation IAM grants
when retiring a disposable test broker.

## Authentication precedence

OCI CLI auth selection order:

1. provider config `credential_source`
2. `ORB_OCI_CREDENTIAL_SOURCE`
3. `OCI_CLI_AUTH`
4. profile fallback

Supported credential source values:

- `instance_principal`
- `resource_principal`
- `api_key`
- `security_token` (requires a session profile)
- `profile`
- `default`

## Template contract (OCI)

See [Compute templates and Flex sizing](../providers/oci/templates.md) for AMD
E5/E6, Intel, Ampere, Standard, and DenseIO coverage, including image architecture
and DenseIO sizing requirements.

Required fields for reliable launches:

- `image_id` from template defaults
- `subnet_ids[0]` from template defaults
- `compartment_id` from template defaults
- shape from the template `machine_types`, `shape`, `instance_type`, or defaults

Recommended:

- `ssh_authorized_keys`
- `user_data` (base64 cloud-init)

## Runtime flow

For `BM.Optimized3.36` workers in an RDMA network group, see
[Compute Clusters and RDMA](../providers/oci/compute-clusters.md). The
`oci-bm-optimized3-rdma` example requires its own HPC image, compute cluster OCID,
and matching availability domain; replace its placeholders before requesting nodes.

After filling the deployment defaults in `config/config.json`, generate and
list templates. Generation writes the provider-owned catalog into the configured
template directory; defaults are resolved when those templates are loaded.

```bash
export ORB_CONFIG_DIR="$PWD/config"
orb --config "$ORB_CONFIG_DIR/config.json" templates generate
orb --config "$ORB_CONFIG_DIR/config.json" templates list
orb --config "$ORB_CONFIG_DIR/config.json" templates validate oci-vm-flex-ondemand-small
```

Skip generation if you already copied a prepared `oci_templates.json` catalog;
generation replaces deployment-specific edits such as a larger boot volume.

The following commands provision and terminate real instances:

```bash
orb --config "$ORB_CONFIG_DIR/config.json" machines request oci-vm-flex-ondemand-small 1 --provider-name oci-default
orb --config "$ORB_CONFIG_DIR/config.json" requests show --request-id <request-id> --provider-name oci-default
orb --config "$ORB_CONFIG_DIR/config.json" machines return <instance-ocid> --provider-name oci-default
```

Edit OCI-specific fields in the generated catalog. Generic template create/update
commands retain their existing upstream behavior.

## Integration notes

- OCI-specific defaults, CLI options, template fields, and HostFactory mappings
  are registered by `OCIPlugin`; shared bootstrap and orchestration require no
  OCI-specific branches.
- Template input accepts canonical `machine_image`, `machine_type`, and
  `machine_disk_size_gb` as well as the existing OCI catalog fields.
- Live launch results require asynchronous polling. Status and resource discovery
  return ORB's flat instance-list format, normalized machine states, and the
  `ProviderFulfilment` verdict required by the current request state machine.
- OCI compute operations run in worker threads so CLI execution does not block
  the async request loop. The broker's dry-run mode is preserved across threads.
- Explicit API-key paths expand user/environment shorthand and relative paths.
  Credential validation rejects paths that do not identify an existing file.
- Provider-specific fields are loaded through the OCI template extension. Shared
  template authoring, defaults resolution, and scheduler behavior are unchanged.
- Dry-run operations never invoke the OCI CLI. Unit tests mock cloud responses;
  they do not establish that IAM, quotas, images, or network configuration work
  in a particular tenancy. Test environment variables do not enable simulation;
  mock workers require an explicit dry-run or mock request.
- OCI command failures report the subcommand, exit code, and structured service
  code/status when available. Command arguments, launch payloads, and raw CLI
  output are omitted because they can contain bootstrap data or keys. Timeouts
  and execution failures also omit the underlying command and output.
- API-key configuration is forwarded via documented child-process
  [OCI CLI environment variables](https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/clienvironmentvariables.htm).
  NSGs are forwarded using the documented
  [instance launch options](https://docs.oracle.com/en-us/iaas/tools/oci-cli/latest/oci_cli_docs/cmdref/compute/instance/launch.html).
- Pricing figures are static estimates inherited from the original implementation;
  use template pricing overrides for your applicable rates.

## Validation checklist

- run OCI unit tests and provider registration tests
- verify request status transitions complete successfully
- confirm routing fields are OCI:
  - `provider_type=oci`
  - `provider_api=OCICompute`

### Live one-machine canary

Use an isolated default-scheduler config and generated template catalog. Fill in
the real image, subnet, compartment, shape, and availability domain first. Run this
from the repository root, with the ORB virtual environment activated and the
built ORB CLI and OCI CLI available on PATH. If OCI CLI is installed in a separate
virtual environment, keep the ORB environment first: prepending the OCI CLI
environment alone also changes which `python` runs and can cause
`ModuleNotFoundError: No module named 'orb'`.

```bash
# Adapt the OCI CLI environment path if needed.
export PATH="$VIRTUAL_ENV/bin:$HOME/.venvs/orb-oci-cli/bin:$PATH"
hash -r
"$VIRTUAL_ENV/bin/python" tests/providers/oci/live/gate.py --config /path/to/config.json --template-file /path/to/oci_templates.json --provider oci-default --orb-bin "$VIRTUAL_ENV/bin/orb" --counts 1 --report-file work/oci-canary-report.json
```

Replace the provider name with the name from your config. The script updates the
selected catalog entry temporarily with a unique `orb_canary_run` tag, requests one machine, checks
its routing and private IP, verifies OCI reports it running, then returns it and
waits for OCI to confirm termination. It needs permission to list instances in the
test compartment for tag-scoped recovery after partial launches or lost responses.
Running status resolves the primary VNIC's addresses and, when configured, its
private DNS name using the subnet's real DNS domain.

Failures and interrupts trigger cleanup of this run's tagged instances and write a
`hold` report. Direct OCI termination is attempted if ORB return fails; such a
fallback keeps the gate failed. Cleanup failures are recorded for manual recovery.
The original catalog is restored after the test; avoid editing it during the run.
The default count is one; mock IDs and skipped cleanup cannot pass this gate.
The same gate is available through the opt-in live test:

```bash
export ORB_OCI_LIVE_CONFIG=/path/to/config.json
export ORB_OCI_LIVE_TEMPLATES=/path/to/oci_templates.json
python -m pytest tests/providers/oci/live --live -q
```
This check does not establish bootstrap completion, workload execution, or actual
Symphony worker registration; verify those separately before production use.

## Troubleshooting

- `...config/config.json not found`: confirm `ORB_CONFIG_DIR` points to your
  deployment directory and that its standard `config.json` exists. A custom
  filename alone can leave early discovery using defaults, even with `--config`.
- `ocid1.instance...mock...` should only appear in explicit mock/dry-run mode.
  In live mode, OCI launch failures are returned as provider errors.
- auth, region, subnet/AD, or policy mismatches can surface as
  `NotAuthorizedOrNotFound` during OCI CLI verification.

### Compute launch transport

Worker launches use OCI CLI `raw-request` to send the `opc-retry-token` header;
the high-level `compute instance launch` command does not expose that header.
The JSON launch body, including bootstrap metadata, is passed through a temporary
file restricted to the current user and removed after the command finishes.
Other lifecycle operations use the corresponding Compute CLI commands.

The launch endpoint uses the configured region and `realm_domain_component`,
which defaults to `oraclecloud.com` for the commercial realm. Set this provider
configuration field to the realm DNS suffix when using another OCI realm.
