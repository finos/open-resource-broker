#!/usr/bin/env bash
# Regenerate the hash-locked CI tool requirements in .github/requirements/.
#
# Each <tool>.in holds the single pinned tool; <tool>.txt is its fully
# resolved, hash-pinned lock, installed in CI with `--require-hashes --no-deps`.
# To bump a tool: edit its .in file, run this script, commit both files.
#
# A <tool>.override file, if present, replaces the version constraints that the
# tool itself declares for its dependencies. It is used to take a patched
# release of a transitive dependency the tool pins to a vulnerable one; the
# lock is installed with --no-deps, so only the resolved set matters at runtime.
#
# Limits of the lock: actionlint-py downloads the actionlint binary from
# GitHub at install time. The hash covers the Python wrapper only, NOT that
# binary. It is kept because the wrapper pin still stops a replaced PyPI
# release; do not treat the binary as verified.
set -euo pipefail

cd "$(dirname "$0")/../../.github/requirements"
for in_file in *.in; do
  # --no-config keeps the project's [tool.uv] overrides out of the locks.
  override=()
  if [[ -f "${in_file%.in}.override" ]]; then
    override=(--override "${in_file%.in}.override")
  fi
  uv pip compile --quiet --no-config --universal --generate-hashes --python-version 3.12 \
    "${override[@]}" "$in_file" -o "${in_file%.in}.txt"
done
