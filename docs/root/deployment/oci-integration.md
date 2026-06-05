# OCI Integration Guide

This guide documents OCI provider support in Open Resource Broker and the
recommended operational flow.

## Overview

OCI support includes:

- template lifecycle operations (validate, create, update, list, show, delete)
- machine acquire and return workflows through OCI compute
- provider routing via `provider_name=oci-default`, `provider_type=oci`,
  `provider_api=OCICompute`

## Provider configuration

Choose exactly one OCI config baseline:

```bash
# Remote/production: ORB runs on OCI Compute with instance principal auth.
cp config/oci_config.remote.example.json config/oci_config.json

# Local/dev: ORB runs on a workstation with an OCI CLI profile.
cp config/oci_config.local.example.json config/oci_config.json
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

OCI templates keep compute-grid choices in `config/oci_templates.json`. Deployment-specific values such as image OCID, subnet OCID, compartment OCID, NSGs, SSH keys, tags, and user data live in `provider_defaults.oci.template_defaults` in the config file.

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
- `profile`
- `default`

## Template contract (OCI)

Required fields for reliable launches:

- `image_id` from template defaults
- `subnet_ids[0]` from template defaults
- `compartment_id` from template defaults
- shape from the template `machine_types`, `shape`, `instance_type`, or defaults

Recommended:

- `ssh_authorized_keys`
- `user_data` (base64 cloud-init)

## Runtime flow

Typical CLI sequence:

```bash
orb --config config/oci_config.json templates validate --file config/oci_templates.json --provider oci-default
orb --config config/oci_config.json templates create --file config/oci_templates.json --provider oci-default
orb --config config/oci_config.json machines request --template-id oci-vm-flex-ondemand-small --count 1 --provider oci-default
orb --config config/oci_config.json requests show --request-id <req-id> --provider oci-default
orb --config config/oci_config.json machines return --machine-id <ocid1> --machine-id <ocid2> --provider oci-default
```

## Validation checklist

- run OCI unit tests and provider registration tests
- verify request status transitions complete successfully
- confirm routing fields are OCI:
  - `provider_type=oci`
  - `provider_api=OCICompute`

## Troubleshooting

- `...config/config.json not found` is informational when `--config` is
  passed explicitly.
- `ocid1.instance...mock...` should only appear in explicit test/dry-run mode.
  In live mode, OCI launch failures are returned as provider errors.
- auth, region, subnet/AD, or policy mismatches can surface as
  `NotAuthorizedOrNotFound` during OCI CLI verification.
