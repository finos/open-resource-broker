"""AWS AMI Resolution service for AWS provider."""

from domain.template.image_resolver import ImageResolver


class AWSAMIResolver(ImageResolver):
    """
    AWS-specific implementation for resolving AMI IDs from various formats.

    This service handles AWS-specific AMI resolution including:
    - Direct AMI IDs (ami-xxxxxxxx)
    - SSM parameter paths (/aws/service/ami-amazon-linux-latest/...)
    - Custom AMI aliases
    """

    def resolve_image_id(self, image_reference: str) -> str:
        """
        Resolve AMI reference to actual AMI ID.

        Args:
            image_reference: AMI ID, alias, or SSM parameter path

        Returns:
            Resolved AMI ID

        Raises:
            ValueError: If AMI cannot be resolved
        """
        if not image_reference:
            raise ValueError("Image reference cannot be empty") from e

        # Direct AMI ID - return as-is
        if image_reference.startswith("ami-"):
            return image_reference

        # SSM parameter path - resolve via SSM
        if image_reference.startswith("/aws/service/"):
            return self._resolve_ssm_parameter(image_reference)

        # Custom alias - resolve via alias mapping
        if self._is_custom_alias(image_reference):
            return self._resolve_custom_alias(image_reference)

        # If we can't resolve it, return as-is and let validation catch it
        return image_reference

    def supports_reference_format(self, image_reference: str) -> bool:
        """
        Check if this resolver supports the given image reference format.

        Args:
            image_reference: Image reference to check

        Returns:
            True if this resolver can handle the reference format
        """
        if not image_reference:
            return False

        # Support direct AMI IDs, SSM parameters, and custom aliases
        return (
            image_reference.startswith("ami-")
            or image_reference.startswith("/aws/service/")
            or self._is_custom_alias(image_reference)
        )

    def _resolve_ssm_parameter(self, ssm_path: str) -> str:
        """
        Resolve SSM parameter to AMI ID.

        Args:
            ssm_path: SSM parameter path

        Returns:
            Resolved AMI ID

        Raises:
            ValueError: If SSM parameter cannot be resolved
        """
        try:
            import boto3
            from botocore.exceptions import ClientError

            ssm_client = boto3.client("ssm")
            response = ssm_client.get_parameter(Name=ssm_path)
            ami_id = str(response["Parameter"]["Value"])

            if not ami_id.startswith("ami-"):
                raise ValueError(
                    f"SSM parameter {ssm_path} did not return a valid AMI ID: {ami_id}"
                )

            return ami_id

        except ClientError as e:
            raise ValueError(f"Failed to resolve SSM parameter {ssm_path}: {e}") from e
        except ImportError:
            raise ValueError("boto3 is required for SSM parameter resolution") from e
        except Exception as e:
            raise ValueError(f"Unexpected error resolving SSM parameter {ssm_path}: {e}") from e

    def _is_custom_alias(self, reference: str) -> bool:
        """
        Check if reference is a custom alias.

        Args:
            reference: Image reference

        Returns:
            True if it's a custom alias format
        """
        # Custom aliases are typically short names without special prefixes
        return (
            len(reference) < 50
            and not reference.startswith(("ami-", "/aws/", "arn:"))
            and reference.replace("-", "").replace("_", "").isalnum()
        )

    def _resolve_custom_alias(self, alias: str) -> str:
        """
        Resolve custom alias to AMI ID.

        Args:
            alias: Custom alias

        Returns:
            Resolved AMI ID

        Raises:
            ValueError: If alias cannot be resolved
        """
        # Common alias mappings - in a real implementation, this might come from
        # configuration
        alias_mappings = {
            "amazon-linux-2": "/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2",
            "ubuntu-20.04": "/aws/service/canonical/ubuntu/server/20.04/stable/current/amd64/hvm/ebs-gp2/ami-id",
            "ubuntu-22.04": "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id",
            "windows-2019": "/aws/service/ami-windows-latest/Windows_Server-2019-English-Full-Base",
            "windows-2022": "/aws/service/ami-windows-latest/Windows_Server-2022-English-Full-Base",
        }

        if alias in alias_mappings:
            # Recursively resolve the SSM parameter
            return self.resolve_image_id(alias_mappings[alias])

        raise ValueError(f"Unknown AMI alias: {alias}") from e
