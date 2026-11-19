"""Pure helpers for the KubeSwift handlers.

KubeSwift (https://github.com/kubeswift-io/kubeswift) runs virtual machines
and OCI-rootfs microVMs as Kubernetes custom resources.  These modules turn
an ORB template into a ``SwiftSandbox`` spec and a ``SwiftSandbox`` object
into ORB machine status.  They import nothing from the Kubernetes client, so
they test without a cluster.
"""

from __future__ import annotations

GROUP = "sandbox.kubeswift.io"
VERSION = "v1alpha1"
SANDBOX_PLURAL = "swiftsandboxes"
SANDBOX_KIND = "SwiftSandbox"
POOL_PLURAL = "swiftsandboxpools"

__all__ = ["GROUP", "POOL_PLURAL", "SANDBOX_KIND", "SANDBOX_PLURAL", "VERSION"]
