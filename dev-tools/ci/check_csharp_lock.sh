#!/usr/bin/env bash
# Guard for sdk/csharp/src/FINOS.OpenResourceBroker/packages.lock.json.
#
# Restore is not run in locked mode: the project references a generated project
# that is not in git, so a lock written without it (as Dependabot does in a clean
# checkout) and one written with it both have to restore. This guard keeps the
# integrity that locked mode gave: after a forced re-evaluation the external
# packages, versions and content hashes must equal the committed lock. Project
# entries are ignored on both sides.
#
# usage: check_csharp_lock.sh [--compare COMMITTED CURRENT]
#   no arguments  restore with --force-evaluate (this overwrites the working
#                 lock) and compare it with the lock committed at HEAD
#   --compare     compare two lock files without restoring (used by the tests)
set -euo pipefail

lock=sdk/csharp/src/FINOS.OpenResourceBroker/packages.lock.json
project=sdk/csharp/src/FINOS.OpenResourceBroker/FINOS.OpenResourceBroker.csproj
normalize='del(.dependencies[][] | select(.type=="Project"))'

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp:?}"' EXIT

# normalize_file IN OUT: write the lock without Project entries, key-sorted.
# Fails on malformed JSON, a lock without a .dependencies object, or empty
# output. Each side goes through a file because a jq failure inside a process
# substitution is not seen by set -e.
normalize_file() {
  jq -S "if (.dependencies | type) == \"object\" then $normalize else error(\"no .dependencies object\") end" \
    "$1" > "$2" 2> "$tmp/jq.err" || {
    echo "cannot read $1 as a packages lock: $(cat "$tmp/jq.err")" >&2
    return 1
  }
  [[ -s "$2" ]] || {
    echo "$1 produced no output when normalized" >&2
    return 1
  }
}

compare() {
  normalize_file "$1" "$tmp/committed.norm" || return 1
  normalize_file "$2" "$tmp/current.norm" || return 1
  diff -u "$tmp/committed.norm" "$tmp/current.norm" || {
    echo "packages.lock.json differs from the committed lock in an external package, version or hash." >&2
    echo "If the change is intended, run 'dotnet restore $project --force-evaluate' and commit the lock." >&2
    return 1
  }
}

if [[ "${1:-}" == "--compare" && $# -eq 3 ]]; then
  compare "$2" "$3"
  exit
fi
if [[ $# -ne 0 ]]; then
  echo "usage: $0 [--compare COMMITTED CURRENT]" >&2
  exit 2
fi

cd "$(git rev-parse --show-toplevel)"
git show "HEAD:$lock" > "$tmp/head.json"
dotnet restore "$project" --force-evaluate
compare "$tmp/head.json" "$lock"
echo "C# packages.lock.json matches the committed lock."
