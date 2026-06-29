"""Kubernetes-specific CLI argument specification.

Mirrors :mod:`orb.providers.aws.cli.aws_cli_spec` for the kubernetes provider.
Registered with :class:`CLISpecRegistry` during provider bootstrap so the
``orb provider add / update`` commands can offer ``--namespace`` and
``--kubeconfig`` flags without the CLI knowing kubernetes specifics.
"""

from __future__ import annotations

import argparse
import re
from typing import Any


class K8sCLISpec:
    """CLI spec for the kubernetes provider."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Add kubernetes-specific arguments to *parser*.

        * ``--namespace`` selects the target namespace for the provider
          instance.  Defaults to ``"default"`` to match the kube-API default
          when the operator omits the flag on ``provider add``.
        * ``--kubeconfig`` points at an explicit kubeconfig file for
          out-of-cluster auth.  When omitted the provider falls back to the
          ``KUBECONFIG`` env var or the auto-detected in-cluster service
          account (see :class:`K8sProviderConfig`).
        * ``--kube-context`` selects a kubeconfig context by name.
        """
        parser.add_argument(
            "--namespace",
            dest="kubernetes_namespace",
            help="Kubernetes namespace for this provider instance (default: 'default').",
        )
        parser.add_argument(
            "--kubeconfig",
            dest="kubernetes_kubeconfig",
            help=(
                "Path to a kubeconfig file (out-of-cluster auth).  "
                "Defaults to the KUBECONFIG env var or in-cluster auth."
            ),
        )
        parser.add_argument(
            "--kube-context",
            dest="kubernetes_context",
            help="kubeconfig context name to select when loading.",
        )

    def extract_config(self, args: argparse.Namespace) -> dict[str, Any]:
        """Return a full provider config dict from parsed args (add path)."""
        return {
            "namespace": getattr(args, "kubernetes_namespace", None) or "default",
            "kubeconfig_path": getattr(args, "kubernetes_kubeconfig", None),
            "context": getattr(args, "kubernetes_context", None),
        }

    def extract_partial_config(self, args: argparse.Namespace) -> dict[str, Any]:
        """Return only the fields the operator explicitly supplied (update path)."""
        result: dict[str, Any] = {}
        if getattr(args, "kubernetes_namespace", None) is not None:
            result["namespace"] = args.kubernetes_namespace
        if getattr(args, "kubernetes_kubeconfig", None) is not None:
            result["kubeconfig_path"] = args.kubernetes_kubeconfig
        if getattr(args, "kubernetes_context", None) is not None:
            result["context"] = args.kubernetes_context
        return result

    def validate_add(self, args: argparse.Namespace) -> list[str]:
        """Validate args for the add command.

        The kubernetes provider has no strictly-required CLI fields: auth and
        namespacing both have sensible auto-detected defaults.  Returns an
        empty list so the add command always proceeds.
        """
        return []

    def generate_name(self, args: argparse.Namespace) -> str:
        """Generate a provider instance name from the kubeconfig context and namespace."""
        try:
            context = getattr(args, "kubernetes_context", None) or ""
            namespace = getattr(args, "kubernetes_namespace", None) or "default"
            sanitized_context = re.sub(r"[^a-zA-Z0-9\-_]", "-", context)
            sanitized_namespace = re.sub(r"[^a-zA-Z0-9\-_]", "-", namespace)
            return f"kubernetes_{sanitized_context}_{sanitized_namespace}"
        except Exception:
            pass  # best-effort name generation; fall back to "kubernetes_default" on any error
        return "kubernetes_default"

    def format_display(self, config: dict[str, Any]) -> list[tuple[str, str]]:
        """Return (label, value) pairs for display."""
        # Use the same en-dash unicode placeholder as AWSCLISpec for unset values
        # so CLI output is visually consistent across providers.
        placeholder = "—"
        return [
            ("Namespace", config.get("namespace", placeholder) or placeholder),
            ("Kubeconfig", config.get("kubeconfig_path", placeholder) or placeholder),
            ("Context", config.get("context", placeholder) or placeholder),
        ]
