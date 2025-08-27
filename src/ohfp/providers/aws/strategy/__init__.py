"""AWS Provider Strategy - AWS implementation of the provider strategy pattern.

This package contains AWS-specific implementations of the provider strategy
pattern, enabling AWS cloud operations to be executed through the integrated
strategy interface while maintaining all AWS-specific capabilities.
"""

from .aws_provider_strategy import AWSProviderStrategy

__all__: list[str] = ["AWSProviderStrategy"]
