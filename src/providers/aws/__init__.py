"""AWS Provider implementation."""

from providers.aws.configuration.config import AWSProviderConfig
from providers.aws.configuration.template_extension import (
    AMIResolutionConfig,
    AWSTemplateExtensionConfig,
)
from providers.aws.registration import (
    get_aws_extension_defaults,
    initialize_aws_provider,
    is_aws_provider_registered,
    register_aws_extensions,
    register_aws_template_factory,
)
from providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

__all__: list[str] = [
    "AWSProviderStrategy",
    "AWSProviderConfig",
    "AWSTemplateExtensionConfig",
    "AMIResolutionConfig",
    "register_aws_extensions",
    "get_aws_extension_defaults",
    "register_aws_template_factory",
    "initialize_aws_provider",
    "is_aws_provider_registered",
]
