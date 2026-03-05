# Git Hooks

Version-controlled git hooks for the Open Resource Broker project.

## Setup

After cloning the repository, run once:

```bash
./dev-tools/setup/install-hooks.sh
```

This configures git to use `.githooks/` instead of `.git/hooks/`.

## Hooks

### pre-commit
Runs quality checks via `pre-commit run` (reads `.pre-commit-config.yaml`, skips manual-stage hooks).

## Adding Custom Hooks

Add a new file to `.githooks/` and it will be version controlled:

```bash
#!/bin/bash
set -e

pre-commit run   # Project checks
# Add more checks here
```

## Manual Execution

```bash
.githooks/pre-commit
```
