"""
AWS Adapters Package

This package contains AWS-specific adapters that implement domain ports.
All adapters follow the naming convention: AWS[Purpose]Adapter
"""

from .aws_provisioning_adapter import AWSProvisioningAdapter
from .machine_adapter import AWSMachineAdapter
from .request_adapter import AWSRequestAdapter
from .template_adapter import AWSTemplateAdapter

__all__: list[str] = [
    "AWSMachineAdapter",
    "AWSProvisioningAdapter",
    "AWSRequestAdapter",
    "AWSTemplateAdapter",
]
