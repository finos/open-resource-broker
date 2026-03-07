"""AWS Provider implementation."""

from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.configuration.template_extension import (
    AMIResolutionConfig,
    AWSTemplateExtensionConfig,
)
from orb.providers.aws.registration import (
    get_aws_extension_defaults,
    initialize_aws_provider,
    is_aws_provider_registered,
    register_aws_extensions,
    register_aws_template_factory,
)
from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

__all__: list[str] = [
    "AMIResolutionConfig",
    "AWSProviderConfig",
    "AWSProviderStrategy",
    "AWSTemplateExtensionConfig",
    "get_aws_extension_defaults",
    "initialize_aws_provider",
    "is_aws_provider_registered",
    "register_aws_extensions",
    "register_aws_template_factory",
]
