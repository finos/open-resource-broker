"""AWS implementation of image resolution service."""

from src.domain.base.ports.logging_port import LoggingPort
from src.domain.exceptions.image_resolution_error import ImageResolutionError
from src.domain.services.image_resolution_service import ImageResolutionService
from src.providers.aws.infrastructure.aws_client import AWSClient
from src.providers.aws.infrastructure.caching.aws_image_cache import AWSImageCache


class AWSImageResolutionService(ImageResolutionService):
    """
    AWS implementation of image resolution service.

    Resolves SSM parameters and aliases to actual AMI IDs using
    AWS SSM API with persistent caching for performance.
    """

    def __init__(
        self,
        aws_client: AWSClient,
        cache: AWSImageCache,
        logger: LoggingPort,
    ):
        self._aws_client = aws_client
        self._cache = cache
        self._logger = logger
        self._ssm_client = aws_client.ssm_client

    def resolve_image_id(self, image_specification: str) -> str:
        """Resolve image specification to AMI ID."""
        # Check cache first
        if cached := self._cache.get(image_specification):
            self._logger.debug(f"Cache hit for image specification: {image_specification}")
            return cached

        # Resolve using AWS SSM
        try:
            resolved_ami = self._resolve_ssm_parameter(image_specification)

            # Cache result
            self._cache.set(image_specification, resolved_ami)
            self._logger.info(f"Resolved {image_specification} to {resolved_ami}")

            return resolved_ami
        except Exception as e:
            raise ImageResolutionError(
                f"Failed to resolve image specification: {e!s}",
                image_specification=image_specification,
            )

    def is_resolution_needed(self, image_specification: str) -> bool:
        """Check if image specification needs resolution."""
        return self.is_resolution_needed_static(image_specification)

    @staticmethod
    def is_resolution_needed_static(image_specification: str) -> bool:
        """Check if image specification needs resolution (no instance required)."""
        # AWS AMI IDs start with 'ami-'
        if image_specification.startswith("ami-"):
            return False
        # SSM parameters typically start with '/'
        if image_specification.startswith("/"):
            return True
        # Assume other formats need resolution
        return True

    def _resolve_ssm_parameter(self, ssm_parameter: str) -> str:
        """AWS-specific SSM parameter resolution."""
        try:
            response = self._ssm_client.get_parameter(Name=ssm_parameter)
            ami_id = response["Parameter"]["Value"]

            if not ami_id.startswith("ami-"):
                raise ImageResolutionError(
                    f"SSM parameter {ssm_parameter} returned invalid AMI ID: {ami_id}",
                    image_specification=ssm_parameter,
                )

            return ami_id
        except self._ssm_client.exceptions.ParameterNotFound:
            raise ImageResolutionError(
                f"SSM parameter not found: {ssm_parameter}",
                image_specification=ssm_parameter,
            )
        except Exception as e:
            raise ImageResolutionError(
                f"Failed to resolve SSM parameter {ssm_parameter}: {e!s}",
                image_specification=ssm_parameter,
            )
