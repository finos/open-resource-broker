# K8s provider test configuration fragment.
# Included by makefiles/providers.mk via -include $(wildcard tests/providers/*/testconf.mk)

# K8s live tests run serially against a single shared cluster.
# Setting WORKERS_k8s to empty removes the -n flag entirely.
WORKERS_k8s :=
