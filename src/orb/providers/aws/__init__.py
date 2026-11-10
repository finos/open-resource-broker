"""AWS Provider implementation."""

from typing import TYPE_CHECKING, Any

from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.configuration.template_extension import (
    AMIResolutionConfig,
    AWSTemplateExtensionConfig,
)
from orb.providers.aws.registration import (
    initialize_aws_provider,
    is_aws_provider_registered,
    register_aws_extensions,
    register_aws_template_factory,
)

if TYPE_CHECKING:
    from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy


def __getattr__(name: str) -> Any:
    # The strategy pulls in the cloud SDK, so it is imported on first access
    # rather than whenever the provider package is imported.
    if name == "AWSProviderStrategy":
        from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

        return AWSProviderStrategy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__: list[str] = [
    "AMIResolutionConfig",
    "AWSProviderConfig",
    "AWSProviderStrategy",
    "AWSTemplateExtensionConfig",
    "initialize_aws_provider",
    "is_aws_provider_registered",
    "register_aws_extensions",
    "register_aws_template_factory",
]
