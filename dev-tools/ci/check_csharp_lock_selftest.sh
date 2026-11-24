#!/usr/bin/env bash
# Prove that check_csharp_lock.sh fails on tampering and passes on the cases it
# must accept. The current lock is the one `dotnet restore --force-evaluate`
# just wrote (CI), or the committed lock with --no-restore (no dotnet needed).
#
#   accepted: the committed lock, the committed lock without its Project entry
#             (what Dependabot writes), the same lock with a Project entry added
#   rejected: a changed contentHash, a changed resolved version, a missing
#             package, malformed JSON, a lock without .dependencies (on either side)
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
lock=sdk/csharp/src/FINOS.OpenResourceBroker/packages.lock.json
project=sdk/csharp/src/FINOS.OpenResourceBroker/FINOS.OpenResourceBroker.csproj
guard=dev-tools/ci/check_csharp_lock.sh

if [[ "${1:-}" != "--no-restore" ]]; then
  dotnet restore "$project" --force-evaluate
fi

work="$(mktemp -d)"
trap 'rm -rf "${work:?}"' EXIT
git show "HEAD:$lock" > "$work/committed.json"

jq "(.dependencies[][] | select(.type != \"Project\") | .contentHash) |= \"AAAA\"" "$work/committed.json" > "$work/hash.json"
jq "(.dependencies[][] | select(.type == \"Direct\") | .resolved) |= \"0.0.1\"" "$work/committed.json" > "$work/version.json"
jq "del(.dependencies[][] | select(.type == \"Transitive\"))" "$work/committed.json" > "$work/missing.json"
jq "del(.dependencies[][] | select(.type == \"Project\"))" "$work/committed.json" > "$work/no-project.json"
jq '.dependencies["net8.0"]["openresourcebroker.sdk"] = {"type": "Project"}' "$work/committed.json" > "$work/with-project.json"

printf '{ not json' > "$work/malformed.json"
printf '{"version": 1}' > "$work/no-dependencies.json"

failures=0
for case in committed no-project with-project; do
  if ! "$guard" --compare "$work/$case.json" "$lock" > /dev/null 2>&1; then
    echo "FAIL: guard rejected the $case lock" >&2
    failures=$((failures + 1))
  fi
done
for case in hash version missing malformed no-dependencies; do
  if "$guard" --compare "$work/$case.json" "$lock" > /dev/null 2>&1; then
    echo "FAIL: guard accepted a lock with a $case change" >&2
    failures=$((failures + 1))
  fi
  # The current side must be rejected the same way as the committed side.
  if "$guard" --compare "$lock" "$work/$case.json" > /dev/null 2>&1; then
    echo "FAIL: guard accepted a current lock with a $case change" >&2
    failures=$((failures + 1))
  fi
done
[[ "$failures" -eq 0 ]] && echo "C# lock guard self-test passed."
exit "$failures"
