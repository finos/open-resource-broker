# Azure provider - authentication

The Azure provider uses two independent credential paths:

* **Azure Resource Manager (ARM)** - every VMSS, SingleVM, and network
  operation authenticates with `azure.identity.DefaultAzureCredential`.
* **CycleCloud** - the `CycleCloud` handler talks to a separate CycleCloud
  REST API, which has its own credential resolution.

## ARM authentication

ORB never asks for a client secret directly. It constructs
`DefaultAzureCredential(managed_identity_client_id=<client_id or unset>)`
and lets the Azure Identity library try its standard chain in order:
environment variables (`AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`/
`AZURE_TENANT_ID`), workload identity, managed identity, shared token
cache, Azure CLI (`az login`), and Azure PowerShell — whichever source is
available first wins. See the
[`DefaultAzureCredential` documentation](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential)
for the exact precedence in your installed SDK version.

### Managed identity selection

| Field (provider config) | Effect                                                              |
|---------------------------|------------------------------------------------------------------------|
| `client_id` unset         | `DefaultAzureCredential()` picks the system-assigned identity (or the only user-assigned identity, if there is exactly one). |
| `client_id` set           | Passed as `managed_identity_client_id`, selecting a specific user-assigned managed identity when more than one is attached to the compute resource running ORB. |

Set `client_id` via the `--azure-client-id` CLI flag, the `client_id`
provider config field, or `ORB_AZURE_CLIENT_ID`.

### Required Azure RBAC

The identity ORB runs as needs, at minimum, a role that grants:

* `Microsoft.Compute/virtualMachines/*` and
  `Microsoft.Compute/virtualMachineScaleSets/*` (read, write, delete) on
  the target resource group.
* `Microsoft.Network/virtualNetworks/subnets/join/action` on the subnet(s)
  referenced by `network_config.subnet_id`.
* `Microsoft.Resources/subscriptions/resourceGroups/read` if
  `subscription_id` validation (`validate_subscription_async`) is used.

`Contributor` on the resource group satisfies all of the above; scope a
custom role down from there for least privilege.

### Credential validation

ORB validates credentials by requesting a token for the
`https://management.azure.com/.default` scope — the standard ARM resource
scope — rather than calling a specific ARM endpoint. A successful token
fetch means the credential chain resolved; it does not guarantee RBAC is
sufficient for every operation.

### Troubleshooting

| Symptom                                                            | Likely cause                                              | Fix                                                                    |
|----------------------------------------------------------------------|--------------------------------------------------------------|---------------------------------------------------------------------------|
| `AuthenticationError` / credential chain exhausted                   | No credential source available (no managed identity, no `az login`, no environment vars) | Run `az login` for local development, or attach a managed identity in Azure. |
| Token fetch succeeds but ARM calls return `403 AuthorizationFailed`  | Identity lacks RBAC on the resource group or subnet          | Grant `Contributor` (or an equivalent custom role) on the resource group and `Network Contributor` on the subnet. |
| Wrong managed identity selected on a VM with multiple identities      | `client_id` not set, so `DefaultAzureCredential` picked an unexpected identity | Set `client_id` to the user-assigned identity's client ID.          |
| `azure-identity dependency error: ...` at startup                    | The `azure` install extra is not installed                   | `pip install "orb-py[azure]"`.                                           |

## CycleCloud authentication

The CycleCloud handler builds its own `httpx` session, independent of the
ARM credential chain. Settings resolve from two layers, in this order
(later wins): the CycleCloud credential file, then the `cyclecloud.*`
provider config fields.

### Credential file

Point `cyclecloud.credential_path` (or
`ORB_AZURE_CYCLECLOUD__CREDENTIAL_PATH`) at a JSON file containing any of:

```json
{
  "url": "https://cyclecloud.example.com",
  "verify_ssl": true,
  "auth_mode": "basic",
  "username": "orb-service",
  "password": "***",
  "aad_scope": "api://cyclecloud-app-id/.default"
}
```

Only these keys are accepted; any other key in the file raises
`CycleCloudConnectionError` at session-build time. Inline
`username`/`password`/`bearer_token` in the provider config's `cyclecloud`
block are rejected outright — they must live in the credential file, kept
out of ORB's own configuration files.

### Auth mode resolution

1. If `username` and `password` are both present in the credential file
   and `auth_mode` is not explicitly `"bearer"`, ORB uses HTTP Basic auth.
2. Otherwise, ORB resolves a Microsoft Entra ID (AAD) bearer token:
   * A `bearer_token` present in the credential file is used directly.
   * Otherwise ORB requests a token using, in order: the configured
     `aad_scope`, the CycleCloud host's own `<scheme>://<host>/.default`
     scope, then `https://management.azure.com/.default` — using the
     same `DefaultAzureCredential` chain described above.
3. `auth_mode="ssh"` is explicitly rejected; configure an API credential
   instead.

### Troubleshooting

| Symptom                                                              | Likely cause                                              | Fix                                                                     |
|-------------------------------------------------------------------------|----------------------------------------------------------------|------------------------------------------------------------------------------|
| `cyclecloud.url is required in provider configuration or its credential file` | Neither the provider config nor the credential file set a URL | Set `cyclecloud.url` or add `"url"` to the credential file.           |
| `Configured CycleCloud credential file contains unsupported fields: ...`     | Credential file has an extra/typo'd key                        | Remove the field; only `url`, `verify_ssl`, `auth_mode`, `username`, `password`, `bearer_token`, `aad_scope` are accepted. |
| `cyclecloud.auth_mode=bearer requested but no bearer token could be resolved` | No bearer token in the file and the AAD credential chain failed | Verify the ARM credential chain works, or set `bearer_token` directly in the credential file for a short-lived test. |
| `cyclecloud.auth_mode=ssh is not supported`                            | Credential file sets `auth_mode: "ssh"`                        | Switch to `basic` or `bearer` auth against the CycleCloud REST API.         |
| CycleCloud inline username/password config is not supported            | `username`/`password`/`bearer_token` set directly under `providers[].config.cyclecloud` | Move the credentials into the file at `credential_path`.           |
