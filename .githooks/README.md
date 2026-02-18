# Git Hooks

Version-controlled git hooks for the Open Resource Broker project.

## Setup

After cloning the repository, run once:

```bash
./dev-tools/scripts/setup-hooks.sh
```

This configures git to use `.githooks/` instead of `.git/hooks/`.

## Hooks

### pre-commit
1. **Beads sync** - Flushes pending changes to JSONL
2. **Quality checks** - Runs `make pre-commit` (lint, format, type check)

### post-merge
- **Beads sync** - Imports updated JSONL after pull/merge

### pre-push
- **Beads validation** - Prevents pushing stale JSONL

### post-checkout
- **Beads sync** - Imports JSONL after branch checkout

### prepare-commit-msg
- **Beads forensics** - Adds agent identity trailers

## Adding Custom Hooks

Edit hooks in `.githooks/` and they'll be version controlled:

```bash
# .githooks/pre-commit
#!/bin/bash
set -e

bd hooks run pre-commit    # Beads integration
make pre-commit            # Project checks
# Add more checks here
```

## Manual Hook Execution

```bash
# Test a hook manually
.githooks/pre-commit

# Or via beads
bd hooks run pre-commit
```
