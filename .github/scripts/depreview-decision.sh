#!/usr/bin/env bash
# Decide what to do about a failed Dependency Review on a Dependabot PR.
#
# Analysis only: reads JSON from the environment, writes files, never calls
# the GitHub API and needs no token. A separate step reads the files and acts.
#
# Usage: depreview-decision.sh OUT_DIR
#
# Environment:
#   VULN        `vulnerable-changes` output of actions/dependency-review-action
#   CHANGES     `dependency-changes` output of the same action
#   MODE_RAW    value of the DEPENDABOT_AUTOCLOSE repository variable (may be empty)
#   GATE_*      "true"/"false" results of the gates the acting step enforces
#               (AUTHOR, ACTOR, TRIGGERING_ACTOR, SAME_REPO, HEAD_REF, NO_KEEP_OPEN)
#
# Files written to OUT_DIR:
#   decision     one word: none | comment | close
#   comment.md   comment body (starts with the dedupe marker)
#   close.md     comment body used when the PR is closed
#   summary.md   job summary (also printed to stdout)
#
# Untrusted fields (names, versions, advisory ids) are validated with anchored
# allowlist regexes; anything that fails validation is dropped. Rendered text
# only ever contains validated fields, wrapped in backticks, and advisory URLs
# are built from validated ids.
set -euo pipefail

out=${1:?usage: depreview-decision.sh OUT_DIR}
mkdir -p "$out"

mode=dry
case "${MODE_RAW:-}" in
  comment | close) mode=$MODE_RAW; mode_src="set to \`$MODE_RAW\`" ;;
  "") mode_src="unset or not readable by this run (Dependabot-triggered runs may not read repository variables)" ;;
  *) mode_src="set to an unrecognised value, treated as \`dry\`" ;;
esac

# shellcheck disable=SC2016  # jq program, not shell
program='
def parse: try fromjson catch null;
def ok_eco: type == "string" and test("\\A[A-Za-z0-9._-]{1,32}\\z");
def ok_name: type == "string"
  and test("\\A@?[A-Za-z0-9][A-Za-z0-9._@/:+~-]{0,127}\\z")
  and (test("@dependabot"; "i") | not);
def ok_ver: type == "string" and test("\\A[A-Za-z0-9._+~!-]{1,64}\\z");
def ok_sev: type == "string" and IN("critical", "high", "moderate", "low");
def ok_ghsa: type == "string" and test("\\AGHSA(-[a-z0-9]{4}){3}\\z");
def advisories:
  [ (.vulnerabilities // []) | if type == "array" then .[] else empty end
    | select(type == "object" and (.severity | ok_sev) and (.advisory_ghsa_id | ok_ghsa))
    | {sev: .severity, id: .advisory_ghsa_id} ] | unique_by(.id);
def entries:
  if type == "array" then
    [ .[] | select(type == "object"
        and (.ecosystem | ok_eco) and (.name | ok_name) and (.version | ok_ver))
      | {ct: .change_type, eco: .ecosystem, name: .name, version: .version, adv: advisories} ]
  else [] end;
def mark(b): if b then "pass" else "FAIL" end;
def gate(v): if v == "true" then "pass" else "FAIL" end;

($ENV.VULN | parse) as $vraw
| ($ENV.CHANGES | parse) as $craw
| ($craw | type == "array") as $changes_ok
| ($vraw | entries | map(select(.adv | length > 0))) as $vuln
| ($vraw | if type == "array" then length else 0 end) as $vraw_n
| ($craw | entries | map(select(.ct == "removed"))) as $removed
| ($craw | if type == "array" then [ .[] | select(type == "object" and .change_type == "removed") ] else [] end) as $removed_raw
| ($craw | if type == "array" then [ .[] | select(type == "object" and .change_type == "added") | [.ecosystem, .name] ] | unique else [] end) as $added_pkgs
| ($vuln | map([.eco, .name]) | unique) as $vuln_pkgs
| {
    r_nonempty: ($vuln | length > 0),
    r_new_advisory: ($changes_ok and any($vuln[]; . as $e
        | any($e.adv[]; . as $a
            | ([$removed[] | select(.eco == $e.eco and .name == $e.name) | .adv[].id]
               | index($a.id)) == null))),
    r_no_fix: ($changes_ok and all($removed_raw[]; ((.vulnerabilities // []) | if type == "array" then length else 1 end) == 0)),
    r_single_pkg: ($changes_ok and ($added_pkgs | length) == 1 and $added_pkgs == $vuln_pkgs)
  } as $r
| (if ($r.r_nonempty | not) then "none"
   elif ($r.r_new_advisory and $r.r_no_fix and $r.r_single_pkg) then "close"
   else "comment" end) as $decision
| [ $vuln[] as $e | $e.adv[] as $a
    | "- `\($e.eco):\($e.name)` `\($e.version)`: [\($a.id)](https://github.com/advisories/\($a.id)) (\($a.sev))" ] as $lines
| (if ($lines | length) > 10
   then $lines[:10] + ["- and \($lines | length - 10) more"]
   else $lines end | join("\n")) as $list
| "<!-- orb-depreview -->\nDependency Review found vulnerabilities in the versions added by this update:\n\n\($list)\n" as $head
| {
    decision: $decision,
    comment: $head,
    close: ($head + "\nThis update adds a vulnerability to a single dependency, so the pull request is closed. Reopen it to override."),
    summary: (
      "## Dependency Review: Dependabot decision\n\n"
      + "- Effective mode: `\($mode)`\n"
      + "- `DEPENDABOT_AUTOCLOSE`: \($mode_src)\n"
      + "- Decision: `\($decision)`\n"
      + "- Acting step runs only in `comment` or `close` mode and only if all gates pass\n\n"
      + "### Gates\n\n"
      + "- author is dependabot[bot]: \(gate($ENV.GATE_AUTHOR))\n"
      + "- actor is dependabot[bot] (id 49699333): \(gate($ENV.GATE_ACTOR))\n"
      + "- triggering actor is dependabot[bot] (id 49699333): \(gate($ENV.GATE_TRIGGERING_ACTOR))\n"
      + "- head repository is this repository: \(gate($ENV.GATE_SAME_REPO))\n"
      + "- head ref starts with dependabot/: \(gate($ENV.GATE_HEAD_REF))\n"
      + "- no keep-open label: \(gate($ENV.GATE_NO_KEEP_OPEN))\n\n"
      + "### Close rules\n\n"
      + "- validated vulnerable list is non-empty: \(mark($r.r_nonempty))\n"
      + "- an added advisory is absent from the removed version: \(mark($r.r_new_advisory))\n"
      + "- PR removes no vulnerable version: \(mark($r.r_no_fix))\n"
      + "- exactly one added package, and it is the vulnerable one: \(mark($r.r_single_pkg))\n\n"
      + "Entries received: \($vraw_n); kept after validation: \($vuln | length).\n"
      + (if ($vuln | length) > 0 then
          "\n### Advisories\n\n\($list)\n\n### Suggested ignore commands\n\nPost manually if the update should be skipped:\n\n```\n"
          + ([ $vuln[] | "@dependabot ignore \(.name) <major|minor|patch> version" ] | unique | join("\n"))
          + "\n```\n"
        else "" end)
    )
  }
'

result=$(jq -n \
  --arg mode "$mode" --arg mode_src "$mode_src" "$program")

printf '%s' "$result" | jq -r '.decision' >"$out/decision"
printf '%s' "$result" | jq -j '.comment' >"$out/comment.md"
printf '%s' "$result" | jq -j '.close' >"$out/close.md"
printf '%s' "$result" | jq -j '.summary' >"$out/summary.md"
cat "$out/summary.md"
