# K8s provider test configuration fragment.
# Included by makefiles/providers.mk via -include $(wildcard tests/providers/*/testconf.mk)

# K8s live tests run against a single shared cluster and namespace, so
# concurrency is bounded rather than open-ended.  Each test uses a unique
# per-test request-id and cleans up only its own resources; the two tests
# that operate on namespace-global objects (a pods=0 ResourceQuota, and the
# orphan garbage collector's whole-namespace sweep) run in their own
# throwaway namespace.  That isolation makes 4-way parallelism safe.
#
# Four workers is the sweet spot for this cluster: it cuts the live suite's
# wall-clock roughly threefold versus serial while staying within the
# cluster's pod-scheduling headroom.  Higher counts (e.g. 7) saturate node
# scheduling capacity, so pods queue behind one another and tests both slow
# down and start timing out — parallelism past this point is neither faster
# nor polite to a shared cluster.
WORKERS_k8s := -n 4
