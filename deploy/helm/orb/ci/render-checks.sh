#!/usr/bin/env bash
# Render-time checks for the ORB chart: values the chart must reject and the
# namespace, RBAC and image settings it must produce. Needs helm, yq and jq.
# Usage: deploy/helm/orb/ci/render-checks.sh
set -euo pipefail

chart="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
failures=0

fail() {
    echo "FAIL: $*" >&2
    failures=$((failures + 1))
}

render() {
    helm template orb "$chart" --namespace team-x "$@"
}

# Render must fail and print a message matching $1.
expect_reject() {
    local pattern="$1"
    shift
    local out
    if out=$(render "$@" 2>&1); then
        fail "expected rejection ($pattern) for: $*"
    elif ! grep -q -- "$pattern" <<<"$out"; then
        fail "rejection for '$*' does not mention '$pattern': $out"
    fi
}

provider_config() {
    yq 'select(.kind == "ConfigMap") | .data["docker.json"]' | jq -c '.provider.providers[0].config'
}

rbac_objects() {
    yq -N 'select(.kind == "Role" or .kind == "RoleBinding" or .kind == "ClusterRole" or .kind == "ClusterRoleBinding") | .kind + "/" + (.metadata.namespace // "")' | sort | tr '\n' ' '
}

expect_eq() {
    local what="$1" got="$2" want="$3"
    if [ "$got" != "$want" ]; then
        fail "$what: got '$got', want '$want'"
    fi
}

tag=(--set image.tag=0.0.0-ci)

# Storage is single-writer.
expect_reject "replicaCount=2" "${tag[@]}" --set replicaCount=2
expect_reject "replicaCount=2" "${tag[@]}" --set storage.type=sqlite --set replicaCount=2

# Default: the provider manages the release namespace, and RBAC covers it.
out=$(render "${tag[@]}")
expect_eq "default provider config" "$(provider_config <<<"$out")" \
    '{"in_cluster":true,"namespace":"team-x","namespaces":["team-x"],"node_watch_enabled":false}'
expect_eq "default rbac" "$(rbac_objects <<<"$out")" "Role/team-x RoleBinding/team-x "

# Explicit namespaces: a Role and RoleBinding in each, bound to the release ServiceAccount.
out=$(render "${tag[@]}" --set 'provider.k8s.namespaces={jobs-a,jobs-b}')
expect_eq "explicit provider config" "$(provider_config <<<"$out")" \
    '{"in_cluster":true,"namespace":"jobs-a","namespaces":["jobs-a","jobs-b"],"node_watch_enabled":false}'
expect_eq "explicit rbac" "$(rbac_objects <<<"$out")" \
    "Role/jobs-a Role/jobs-b RoleBinding/jobs-a RoleBinding/jobs-b "
out=$(yq -N 'select(.kind == "RoleBinding") | .subjects[0].namespace' <<<"$out" | sort -u)
expect_eq "rolebinding subject namespace" "$out" "team-x"

# The release namespace stays the default when it is in the list.
out=$(render "${tag[@]}" --set 'provider.k8s.namespaces={jobs-a,team-x}' | provider_config)
expect_eq "release namespace default" "$(jq -r .namespace <<<"$out")" "team-x"

# Cluster-scoped watch and node watch need the ClusterRole.
expect_reject "rbac.clusterScoped" "${tag[@]}" --set 'provider.k8s.namespaces={*}'
expect_reject "rbac.clusterScoped" "${tag[@]}" --set provider.k8s.nodeWatchEnabled=true
expect_reject "cannot be combined" "${tag[@]}" --set rbac.clusterScoped=true --set 'provider.k8s.namespaces={*,jobs-a}'
out=$(render "${tag[@]}" --set rbac.clusterScoped=true --set 'provider.k8s.namespaces={*}' --set provider.k8s.nodeWatchEnabled=true)
expect_eq "cluster-scoped rbac" "$(rbac_objects <<<"$out")" "ClusterRole/ ClusterRoleBinding/ "
expect_eq "cluster-scoped namespace" "$(provider_config <<<"$out" | jq -r .namespace)" "team-x"
# Users who manage RBAC themselves are not blocked.
render "${tag[@]}" --set rbac.create=false --set 'provider.k8s.namespaces={*}' >/dev/null \
    || fail "rbac.create=false with namespaces [*] must render"

# ClusterRole contents follow the configuration, never more than it needs.
cluster_rules() {
    yq -N -o=json 'select(.kind == "ClusterRole") | .rules' | jq -c '[.[] | {r: .resources, v: .verbs}]'
}
# Explicit namespaces: per-namespace Roles only, even with rbac.clusterScoped set.
out=$(render "${tag[@]}" --set rbac.clusterScoped=true --set 'provider.k8s.namespaces={jobs-a,jobs-b}')
expect_eq "explicit namespaces with clusterScoped" "$(rbac_objects <<<"$out")" \
    "Role/jobs-a Role/jobs-b RoleBinding/jobs-a RoleBinding/jobs-b "
# Node watch alone: a ClusterRole that can only read nodes.
out=$(render "${tag[@]}" --set rbac.clusterScoped=true --set provider.k8s.nodeWatchEnabled=true)
expect_eq "node watch rbac" "$(rbac_objects <<<"$out")" "ClusterRole/ ClusterRoleBinding/ Role/team-x RoleBinding/team-x "
expect_eq "node watch rules" "$(cluster_rules <<<"$out")" '[{"r":["nodes"],"v":["get","list","watch"]}]'
# Wildcard: the full workload rules cluster-wide, with nodes only when the node watch is on.
out=$(render "${tag[@]}" --set rbac.clusterScoped=true --set 'provider.k8s.namespaces={*}')
expect_eq "wildcard rbac" "$(rbac_objects <<<"$out")" "ClusterRole/ ClusterRoleBinding/ "
case "$(cluster_rules <<<"$out")" in
    *'"r":["pods"],"v":["get","list","watch","create","patch","delete"]'*'"r":["jobs"]'*) ;;
    *) fail "wildcard ClusterRole must carry the workload rules: $(cluster_rules <<<"$out")" ;;
esac
case "$(cluster_rules <<<"$out")" in *nodes*) fail "wildcard without node watch must not read nodes" ;; esac

# The AWS provider gets no Kubernetes RBAC.
out=$(render "${tag[@]}" --set provider.type=aws --set rbac.clusterScoped=true)
expect_eq "aws rbac" "$(rbac_objects <<<"$out")" ""

if [ "$failures" -ne 0 ]; then
    echo "$failures render check(s) failed" >&2
    exit 1
fi
echo "all render checks passed"
