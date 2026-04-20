"""SSH key resolution service.

Resolves Azure SSH Public Key resource names to actual key data via
the Compute SDK, keeping infrastructure concerns out of the domain model.
"""

from __future__ import annotations

from typing import Any


def _azure_resource_not_found_error_type() -> type[Exception]:
    """Resolve the Azure SDK's not-found exception lazily."""
    from azure.core.exceptions import ResourceNotFoundError

    return ResourceNotFoundError


def _azure_http_response_error_type() -> type[Exception]:
    """Resolve the Azure SDK's HTTP response exception lazily."""
    from azure.core.exceptions import HttpResponseError

    return HttpResponseError


def resolve_ssh_keys(
    *,
    ssh_key_name: str | None,
    ssh_public_keys: list[str],
    resource_group: str,
    compute_client: Any,
) -> list[str]:
    """Resolve SSH public key data for VM provisioning.

    If *ssh_public_keys* are provided inline they are returned as-is.
    If *ssh_key_name* is set the function fetches the public key data
    from the ``Microsoft.Compute/sshPublicKeys`` resource in the
    given resource group.

    Args:
        ssh_key_name: Name of an Azure SSH Public Key resource.
        ssh_public_keys: Inline SSH public key strings.
        resource_group: Azure resource group containing the SSH key resource.
        compute_client: An Azure ``ComputeManagementClient`` instance.

    Returns:
        A list of SSH public key strings.

    Raises:
        ValueError: If neither *ssh_key_name* nor *ssh_public_keys*
            is provided, or if the named SSH key resource cannot be found.
    """
    if ssh_public_keys:
        return list(ssh_public_keys)

    if not ssh_key_name:
        raise ValueError(
            "Cannot resolve SSH keys: neither 'ssh_key_name' nor "
            "'ssh_public_keys' is set on the template."
        )

    try:
        ssh_resource = compute_client.ssh_public_keys.get(
            resource_group_name=resource_group,
            ssh_public_key_name=ssh_key_name,
        )
        key_data: str = ssh_resource.public_key
        if not key_data:
            raise ValueError(
                f"Azure SSH Public Key resource '{ssh_key_name}' "
                f"in resource group '{resource_group}' exists but "
                f"contains no public key data."
            )
    except _azure_resource_not_found_error_type():
        raise ValueError(
            f"Azure SSH Public Key resource '{ssh_key_name}' "
            f"in resource group '{resource_group}' was not found. "
            f"Ensure the resource exists and the caller has "
            f"'Microsoft.Compute/sshPublicKeys/read' permission."
        )
    except _azure_http_response_error_type() as exc:
        raise ValueError(
            f"Failed to resolve Azure SSH Public Key '{ssh_key_name}' "
            f"in resource group '{resource_group}': {exc}"
        ) from exc

    return [key_data]
