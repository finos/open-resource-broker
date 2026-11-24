#!/usr/bin/env bash
# Regenerate the hash-locked CI tool requirements in .github/requirements/.
#
# Each <tool>.in holds the single pinned tool; <tool>.txt is its fully
# resolved, hash-pinned lock, installed in CI with `--require-hashes --no-deps`.
# To bump a tool: edit its .in file, run this script, commit both files.
#
# Dependabot's uv ecosystem refreshes these locks by re-running `uv pip compile`
# with --universal, --generate-hashes and --python-version taken from the lock
# header. It cannot replay --override or --constraint, so a tool that needs them
# is a uv project instead (see dev-tools/ci/semgrep, locked with `uv lock`).
#
# `--check` regenerates every lock in a scratch directory, seeded with the
# committed lock so existing pins stay, and fails if the result differs.
#
# A "# python-version: X.Y" line in a .in file sets the minimum Python the lock
# resolves for (default 3.12). Locks installed on developer machines or in
# images with an older interpreter declare the older version there.
#
# Limits of the lock: actionlint-py downloads the actionlint binary from
# GitHub at install time. The hash covers the Python wrapper only, NOT that
# binary. It is kept because the wrapper pin still stops a replaced PyPI
# release; do not treat the binary as verified.
set -euo pipefail

check=false
if [[ "${1:-}" == "--check" ]]; then
  check=true
elif [[ $# -gt 0 ]]; then
  echo "usage: $0 [--check]" >&2
  exit 2
fi

root="$(cd "$(dirname "$0")/../.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "${scratch:?}"' EXIT

# compile DIR IN_FILE PYTHON_VERSION OUT_FILE
# --no-config keeps the project's [tool.uv] settings out of the locks.
compile() {
  (cd "$1" && uv pip compile --quiet --no-config --universal --generate-hashes \
    --python-version "$3" "$2" -o "$4")
}

# regenerate DIR IN_FILE PYTHON_VERSION: rewrite DIR/<name>.txt, or with --check
# compare a seeded copy made in a scratch directory outside the repository.
regenerate() {
  local dir="$1" in_file="$2" py="$3" out="${2%.in}.txt"
  if [[ "$check" == false ]]; then
    compile "$dir" "$in_file" "$py" "$out"
    return
  fi
  local work
  work="$(mktemp -d -p "$scratch")"
  cp "$dir/$in_file" "$dir/$out" "$work/"
  compile "$work" "$in_file" "$py" "$out"
  diff -u "$dir/$out" "$work/$out" || {
    echo "$dir/$out is out of date; run dev-tools/ci/update_tool_locks.sh" >&2
    return 1
  }
}

status=0
cd "$root/.github/requirements"
for in_file in *.in; do
  py="$(sed -n 's/^# python-version: *//p' "$in_file" | head -n 1)"
  regenerate "$PWD" "$in_file" "${py:-3.12}" || status=1
done

# MicroVM worker image (the worker image installs Python 3.11); the lock sits next
# to its Dockerfile.
regenerate "$root/tests/e2e/microvm/worker" requirements.in 3.11 || status=1
exit "$status"
