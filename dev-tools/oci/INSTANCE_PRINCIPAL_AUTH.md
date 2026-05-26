# OCI instance principal authentication (ORB)

How ORB uses **OCI instance principal** on OCI compute hosts (no `~/.oci/config` required).

## Problem

On OCI VMs, auth is usually **instance principal**. The OCI CLI supports this via:

```bash
oci --auth instance_principal ...
```

ORB invokes the OCI CLI as a subprocess. Without `--auth instance_principal`, the CLI looks for `~/.oci/config`. That fails or hangs interactively, and the OCI handler **falls back to mock instance OCIDs** (`ocid1.instance.oc1..mock1`).

Mock IDs mean the request “completed” in ORB but **no real VM was created**. Always verify IDs look like `ocid1.instance.oc1.phx....`, not `...mock...`.

## Solution (ORB 1.6+)

1. Configure ORB with `credential_source: instance_principal`.
2. Put `oci` on `PATH` (project venv).
3. Create a **dynamic group** for the broker instance(s).
4. Attach IAM policies in the **tenancy root** (see below).
5. Match templates to the target subnet, image, compartment, and availability domain.

---

## ORB provider config

Copy `config/oci_config.example.json` to `config/oci_config.json` (gitignored) or merge into `config/config.json`:

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

**Do not** set `"profile": "DEFAULT"` on instance-principal hosts unless you also maintain `~/.oci/config`.

### Supported `credential_source` values

Passed to `oci --auth` by `oci_cli_auth.py`:

| Value | Use case |
|--------|-----------|
| `instance_principal` | OCI compute instance (this pattern) |
| `resource_principal` | OCI Functions / resource principal |
| `api_key` | Explicit API key fields in config |
| `profile` / `default` | Local dev with `~/.oci/config` |

### Environment overrides (optional)

| Variable | Purpose |
|----------|---------|
| `ORB_OCI_CREDENTIAL_SOURCE` | Overrides config `credential_source` |
| `OCI_CLI_AUTH` | Fallback if config omits `credential_source` |

Precedence: **config `credential_source` → `ORB_OCI_CREDENTIAL_SOURCE` → `OCI_CLI_AUTH` → `--profile`**.

ORB provider entries nest settings under `"config"`; `create_oci_strategy()` must unwrap that inner object (`registration.py`).

---

## Shell and OCI CLI setup

Install the CLI in the project venv and expose it on `PATH`:

```bash
cd open-resource-broker
uv sync --group dev --extra cli
uv pip install oci-cli
```

Example `~/.bashrc.d/oci.sh`:

```bash
ORB_VENV="/home/opc/open-resource-broker/.venv"
if [[ -d "$ORB_VENV/bin" ]]; then
  export PATH="$ORB_VENV/bin:$PATH"
fi
export OCI_CLI_AUTH="${OCI_CLI_AUTH:-instance_principal}"
export OCI_REGION="${OCI_REGION:-us-phoenix-1}"
```

Source before running `oci` or `orb`:

```bash
source ~/.bashrc.d/oci.sh
```

---

## IAM: dynamic group

Create in **Identity → Dynamic groups** (tenancy or identity domain).

**Name:** e.g. `orb-broker-instances`

**Matching rule** (single broker VM — tightest):

```text
ALL {instance.id = '<broker-instance-ocid>'}
```

Or all instances in the workload compartment:

```text
ALL {instance.compartment.id = '<compartment-ocid>'}
```

Get broker metadata:

```bash
curl -s -H "Authorization: Bearer Oracle" http://169.254.169.254/opc/v2/instance/ | python3 -m json.tool
```

On identity-domain tenancies, policies may need `Allow dynamic-group '<domain>'/'orb-broker-instances' to ...` instead of `Allow dynamic-group orb-broker-instances to ...`.

---

## IAM: policy statements

Create the policy in the **tenancy root** (recommended), with statements scoped to your workload compartment (example name: `Agents`).

Replace:

- `Agents` → exact compartment **name** from the Console (case-sensitive).
- `<subnet-ocid>` → subnet where VMs are launched.

### Dynamic group (caller on the broker VM)

```text
Allow dynamic-group orb-broker-instances to manage instance-family in compartment Agents

Allow dynamic-group orb-broker-instances to use instances in compartment Agents

Allow dynamic-group orb-broker-instances to manage volume-family in compartment Agents

Allow dynamic-group orb-broker-instances to use subnets in compartment Agents where target.subnet.id = '<subnet-ocid>'

Allow dynamic-group orb-broker-instances to use vnics in compartment Agents

Allow dynamic-group orb-broker-instances to use network-security-groups in compartment Agents

Allow dynamic-group orb-broker-instances to use private-ips in compartment Agents

Allow dynamic-group orb-broker-instances to use virtual-network-family in compartment Agents

Allow dynamic-group orb-broker-instances to inspect virtual-network-family in compartment Agents

Allow dynamic-group orb-broker-instances to use instance-images in tenancy

Allow dynamic-group orb-broker-instances to read app-catalog-listing in tenancy
```

### Service principal (required for image launch)

Launch creates boot volumes and VNICs **as the Compute service**. Without these, `oci compute instance launch` often returns `NotAuthorizedOrNotFound` even when volume create and subnet get succeed.

```text
Allow service compute to manage instance-family in compartment Agents

Allow service compute to manage volumes in compartment Agents

Allow service compute to use volumes in compartment Agents

Allow service compute to use subnets in compartment Agents where target.subnet.id = '<subnet-ocid>'

Allow service compute to use vnics in compartment Agents

Allow service compute to use network-security-groups in compartment Agents

Allow service compute to use private-ips in compartment Agents

Allow service compute to use virtual-network-family in compartment Agents
```

### Why both `manage` and `use`?

| Permission | Typical symptom if missing |
|------------|---------------------------|
| `manage instance-family` | Cannot update instances; some APIs fail |
| `use instances` | **`launch_instance` fails** (launch uses the `use instances` verb) |
| `manage volume-family` | Block/boot volume create fails |
| `use instance-images in tenancy` | Image launch fails (do not rely on `read` alone) |
| `use virtual-network-family` | Launch fails after volumes/subnet checks pass |
| `Allow service compute ...` | Launch fails with `NotAuthorizedOrNotFound` on image path |

**Note:** `instance update` succeeding does **not** prove launch works — update uses `manage`, launch needs `use instances` plus service-compute lines.

### Statements that break the policy builder

Do **not** use (OCI returns `InvalidParameter: No permissions found`):

```text
Allow dynamic-group ... to inspect availability-domains in tenancy
```

Avoid on `inspect virtual-network-family`:

```text
... where target.vcn.id = 'ocid1.vcn...'
```

Use subnet-scoped `use subnets ... where target.subnet.id = '...'` instead.

### Dev-only shortcut

If policies are still unclear, temporarily add (remove after debugging):

```text
Allow dynamic-group orb-broker-instances to manage all-resources in compartment Agents
```

---

## Templates

`config/oci_templates.json` must match the IAM-scoped subnet and region.

- `subnet_ids` → OCID in the policy `where target.subnet.id = '...'` clause.
- `metadata.compartment_id` → compartment where instances are created.
- `availability_domain` → from instance metadata (skips subnet GET if IAM read is limited).

```json
"subnet_ids": [
  "ocid1.subnet.oc1.phx.aaaaaaaa..."
],
"metadata": {
  "compartment_id": "ocid1.compartment.oc1..aaaa...",
  "availability_domain": "pILZ:PHX-AD-1"
},
"availability_domain": "pILZ:PHX-AD-1"
```

Sync after edits:

```bash
orb --config config/oci_config.json templates update \
  --template-id oci-flex-2x16-template \
  --file config/oci_templates.json \
  --provider oci-default
```

---

## Validation

### 1. Auth smoke test

```bash
source ~/.bashrc.d/oci.sh
oci iam region list --auth instance_principal --region us-phoenix-1 --query 'data[?name==`us-phoenix-1`]'
```

### 2. Permission matrix (before ORB)

| Command | Expect |
|---------|--------|
| `oci network subnet get --subnet-id <subnet> ...` | Success |
| `oci compute image get --image-id <image> ...` | Success |
| `oci bv volume create ...` | Success (needs `manage volume-family`) |
| `oci compute instance update --instance-id <self> ...` | Success (`manage instance-family`) |
| `oci compute instance launch ... --image-id <image> ...` | Success → real `ocid1.instance.oc1....` |

Launch command (full flags — do not omit `--auth`):

```bash
oci compute instance launch \
  --availability-domain "pILZ:PHX-AD-1" \
  --compartment-id "<compartment-ocid>" \
  --shape "VM.Standard.E5.Flex" \
  --shape-config '{"ocpus":1,"memoryInGBs":16}' \
  --subnet-id "<subnet-ocid>" \
  --assign-public-ip false \
  --image-id "<image-ocid>" \
  --display-name orb-smoke-test \
  --auth instance_principal \
  --region us-phoenix-1
```

IAM changes can take **5–15 minutes** to propagate.

### 3. ORB template + request

```bash
cd open-resource-broker
source ~/.bashrc.d/oci.sh

orb --config config/oci_config.json templates validate \
  --template-id oci-flex-2x16-template \
  --file config/oci_templates.json \
  --provider oci-default

orb --config config/oci_config.json machines request \
  --template-id oci-flex-2x16-template \
  --count 1 \
  --provider oci-default
```

Check status (use **`requests show`**, not `machines request show`):

```bash
orb --config config/oci_config.json requests show \
  --request-id <request-id> \
  --provider oci-default
```

Or watch until complete:

```bash
orb --config config/oci_config.json requests watch \
  <request-id> \
  --provider oci-default
```

### 4. Pre-live gate

```bash
.venv/bin/python dev-tools/oci/run_pre_live_gate.py \
  --config config/oci_config.json \
  --template-file config/oci_templates.json \
  --provider oci-default \
  --template-id oci-flex-2x16-template \
  --orb-bin "$(pwd)/.venv/bin/orb" \
  --counts 1 \
  --verify-oci-cli
```

The gate fails if any instance ID contains `mock` (unless `--allow-mock-ocids`).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|----------------|-----|
| Mock OCIDs (`...mock1`) | Launch failed; ORB fallback | Fix IAM; confirm `credential_source`; check `logs/orb.log` for `OCI live launch failed` |
| Hang on `oci` | Missing config, interactive prompt | `credential_source: instance_principal`; remove `profile` |
| `NotAuthorizedOrNotFound` on launch; volume create OK | Missing `use instances`, `use virtual-network-family`, or **service compute** policies | Add policy blocks above |
| `NotAuthorizedOrNotFound` on launch; instance update OK | Missing **`use instances`** (manage ≠ use for launch) | Add `use instances in compartment ...` |
| Policy builder: `No permissions found` | Invalid statement grammar | Remove `inspect availability-domains`; avoid `where target.vcn.id` on `virtual-network-family` |
| Subnet get 404 | Wrong subnet or IAM read | Align template subnet OCID with policy; set `availability_domain` on template |
| Wrong region | Config vs OCID mismatch | Set `region` to image/subnet region (e.g. `us-phoenix-1`) |
| Launch CLI typo | Bad `--region` or missing `--auth` | Use `--region us-phoenix-1` and `--auth instance_principal` |

### Log line to search

```bash
grep "OCI live launch failed" logs/orb.log | tail -1
```

Shows the exact `oci` command and stderr from the handler.

---

## Code references

- `src/orb/providers/oci/oci_cli_auth.py` — builds `--auth` / `--profile` args
- `src/orb/providers/oci/handlers/oci_compute_handler.py` — launch/terminate/status; mock fallback on CLI failure
- `src/orb/providers/oci/configuration/config.py` — `credential_source` field + validation
- `dev-tools/oci/run_pre_live_gate.py` — end-to-end smoke with mock OCID rejection

---

## Implementation status

- [x] `credential_source` in `OCIProviderConfig`
- [x] OCI CLI subprocess passes `--auth instance_principal`
- [x] Pre-live gate uses same auth helper
- [x] `create_oci_strategy` unwraps nested provider `config` dict
- [x] AD resolution skips subnet API when `availability_domain` is set
- [x] IAM policy reference (dynamic group + service compute) documented here
