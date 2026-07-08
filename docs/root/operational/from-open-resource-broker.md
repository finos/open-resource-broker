# Migrating from `open-resource-broker` to `orb-py[k8s-legacy]`

This guide is for operators who deployed the legacy `open-resource-broker` PyPI package and need to upgrade to the consolidated `orb-py` distribution.

## What changed

The Symphony-on-Kubernetes HostFactory plugin previously published as a standalone `open-resource-broker` package is now bundled with `orb-py` as an optional install extra at the `orb.k8s_legacy` sub-module path.  There is a single wheel: `orb-py`.  The heavy dependencies (kubernetes, fastapi, uvicorn, sqlalchemy, alembic, pg8000, prometheus-client, watchdog, inotify, and others) are behind the `[k8s-legacy]` extra so that operators running only the modern AWS provider do not pay for them.

The runtime semantics are unchanged: same filesystem workdir layout, same push-based Kubernetes watchers, same event log format, same HostFactory API output.

## Install

| Before | After |
|--------|-------|
| `pip install open-resource-broker` | `pip install "orb-py[k8s-legacy]"` |

The `open-resource-broker` package is no longer published to PyPI.  Pin to an older release only as a rollback option (see [Rollback](#rollback)).

## CLI commands

The standalone entry-point binaries (`open-resource-broker`, `open-resource-broker-admin`, `open-resource-broker-utils`, `open-resource-broker-events`) are replaced by subcommand groups under the unified `orb` binary.

### Core HostFactory verbs

| Before | After |
|--------|-------|
| `open-resource-broker request-machines '{...}'` | `orb k8s-legacy request-machines '{...}'` |
| `open-resource-broker get-request-status '{...}'` | `orb k8s-legacy get-request-status '{...}'` |
| `open-resource-broker request-return-machines '{...}'` | `orb k8s-legacy request-return-machines '{...}'` |
| `open-resource-broker get-return-requests '{...}'` | `orb k8s-legacy get-return-requests '{...}'` |
| `open-resource-broker get-available-templates` | `orb k8s-legacy get-available-templates` |
| `open-resource-broker watch pods` | `orb k8s-legacy watch pods` |
| `open-resource-broker watch request-machines` | `orb k8s-legacy watch request-machines` |
| `open-resource-broker watch request-return-machines` | `orb k8s-legacy watch request-return-machines` |
| `open-resource-broker watch events` | `orb k8s-legacy watch events` |
| `open-resource-broker watch nodes` | `orb k8s-legacy watch nodes` |
| `open-resource-broker watch kube-events` | `orb k8s-legacy watch kube-events` |
| `open-resource-broker run-cron` | `orb k8s-legacy run-cron` |

### Admin

| Before | After |
|--------|-------|
| `open-resource-broker-admin <verb>` | `orb k8s-legacy admin <verb>` |

### Utils and events-db

| Before | After |
|--------|-------|
| `open-resource-broker-utils` | `orb k8s-legacy utils` |
| `open-resource-broker-events transform` | `orb k8s-legacy events-db transform` |

The `utils` server takes its configuration via flags (`--host`, `--port`, `--workdir`, `--platform`, `--cluster`, `--region`, `--namespace`, `--bucket`); it is not a subcommand group.  Pass flags directly: `orb k8s-legacy utils --host 0.0.0.0 --port 8080`.

## HF shell scripts

The five HostFactory provider scripts at `${HF_CONFDIR}/providers/k8s-hf/scripts/` now call `orb k8s-legacy <verb>` internally instead of `hostfactory <verb>` (or `open-resource-broker <verb>`).

These scripts are repository artefacts, not Python package data, and are not bundled in the `orb-py` wheel.  After upgrading, replace your deployed copies with the updated versions from the repository at `k8s-legacy/hostfactory/providers/k8s-hf/scripts/`:

```bash
# From a checkout of finos/open-resource-broker at the release tag:
cp k8s-legacy/hostfactory/providers/k8s-hf/scripts/*.sh "${HF_CONFDIR}/providers/k8s-hf/scripts/"
```

Or fetch the five scripts (`requestMachines.sh`, `getRequestStatus.sh`, `requestReturnMachines.sh`, `getReturnRequests.sh`, `getAvailableTemplates.sh`) individually from the release tarball or the GitHub source tree.

## Operator daemon services

If you have systemd units, supervisord configs, or similar process managers running the watcher daemons, update the `ExecStart` (or equivalent) line for each daemon.

| Daemon | Before | After |
|--------|--------|-------|
| Pod watcher | `/usr/local/bin/open-resource-broker watch pods` | `/usr/local/bin/orb k8s-legacy watch pods` |
| Request watcher | `/usr/local/bin/open-resource-broker watch request-machines` | `/usr/local/bin/orb k8s-legacy watch request-machines` |
| Return watcher | `/usr/local/bin/open-resource-broker watch request-return-machines` | `/usr/local/bin/orb k8s-legacy watch request-return-machines` |
| Cron runner | `/usr/local/bin/open-resource-broker run-cron` | `/usr/local/bin/orb k8s-legacy run-cron` |

Adjust the binary path to match your virtual environment if `orb` is not on the system `PATH`.

After updating the service definition, reload and restart:

```bash
systemctl daemon-reload
systemctl restart hostfactory-watch-pods.service
# repeat for each daemon unit
```

## Python imports

This applies only if you embed the legacy plugin as a library.  Operator deployments that invoke the CLI exclusively are unaffected.

| Before | After |
|--------|-------|
| `from open_resource_broker import ...` | `from orb.k8s_legacy import ...` |
| `from open_resource_broker.api import ...` | `from orb.k8s_legacy.api import ...` |

## Runtime semantics

Unchanged.  The filesystem workdir layout (`/var/tmp/hostfactory` by default), Kubernetes watcher behaviour, event log format, and HostFactory API output are identical to the legacy standalone package.

## Rollback

To roll back to the standalone package, pin to the last published version:

```bash
pip install "open-resource-broker<1.0"
```

Or revert to a previous `orb-py` release that predates the consolidation.  Check the project changelog at the repository root for the exact version boundary.
